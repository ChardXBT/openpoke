"""Optional OpenAI-compatible embeddings for agent search.

Disabled unless ``OPENAI_API_KEY`` is set. Vectors are cached by model+text hash in one
SQLite database as packed float32 blobs instead of thousands of JSON files. The process
memory cache is a bounded LRU; the disk cache is bounded as well, with the coldest rows
evicted first.
"""

from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import struct
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Iterable, Optional

Embedder = Callable[[list[str]], list[list[float]]]
MODEL = "text-embedding-3-small"
_CLIENT = None
_DEFAULT_MEMORY_ENTRIES = 1024
_DEFAULT_DISK_ENTRIES = 50_000
_PREPARE_BATCH = 256
_EMBED_BATCH = 128
_VECTOR_BATCH = 512


def cache_dir() -> Path:
    override = (os.getenv("OPENPOKE_EMBEDDING_CACHE_DIR") or "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data" / "embeddings"


def digest_for(text: str) -> str:
    return hashlib.sha256(f"{MODEL}\0{text}".encode("utf-8")).hexdigest()


def _bounded_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _clean_vector(vector: Iterable[float]) -> list[float]:
    values = [float(item) for item in vector]
    if not values:
        raise ValueError("embedding vectors must be non-empty")
    if not all(math.isfinite(item) for item in values):
        raise ValueError("embedding vectors must contain only finite numbers")
    return values


def _pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(blob: bytes, dimensions: int) -> list[float]:
    if dimensions <= 0 or len(blob) != dimensions * 4:
        raise ValueError("invalid embedding cache entry")
    values = list(struct.unpack(f"<{dimensions}f", blob))
    if not all(math.isfinite(item) for item in values):
        raise ValueError("invalid embedding cache entry")
    return values


class EmbeddingStore:
    """SQLite-backed vector cache with bounded memory and bounded disk rows."""

    def __init__(
        self,
        directory: Optional[Path] = None,
        *,
        memory_entries: Optional[int] = None,
        disk_entries: Optional[int] = None,
    ) -> None:
        self.root = directory or cache_dir()
        self.memory_entries = (
            _bounded_env("OPENPOKE_EMBEDDING_MEMORY_ENTRIES", _DEFAULT_MEMORY_ENTRIES)
            if memory_entries is None
            else max(0, memory_entries)
        )
        self.disk_entries = (
            _bounded_env("OPENPOKE_EMBEDDING_DISK_ENTRIES", _DEFAULT_DISK_ENTRIES)
            if disk_entries is None
            else max(0, disk_entries)
        )
        self._memory: OrderedDict[str, bytes] = OrderedDict()
        self._memory_lock = threading.Lock()
        self.db_path = self.root / "embeddings.sqlite3"
        if self.disk_entries:
            self.root.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS embeddings ("
                    "digest TEXT PRIMARY KEY, "
                    "dimensions INTEGER NOT NULL, "
                    "vector BLOB NOT NULL, "
                    "updated_at REAL NOT NULL, "
                    "last_accessed REAL NOT NULL)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_embeddings_last_accessed "
                    "ON embeddings(last_accessed)"
                )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30.0)

    @property
    def memory_size(self) -> int:
        with self._memory_lock:
            return len(self._memory)

    @property
    def memory_bytes(self) -> int:
        with self._memory_lock:
            return sum(len(blob) for blob in self._memory.values())

    def _memory_get(self, digest: str) -> Optional[bytes]:
        with self._memory_lock:
            blob = self._memory.get(digest)
            if blob is not None:
                self._memory.move_to_end(digest)
            return blob

    def _memory_put_blob(self, digest: str, blob: bytes) -> None:
        if self.memory_entries == 0:
            return
        with self._memory_lock:
            self._memory[digest] = bytes(blob)
            self._memory.move_to_end(digest)
            while len(self._memory) > self.memory_entries:
                self._memory.popitem(last=False)

    def _memory_delete(self, digest: str) -> None:
        with self._memory_lock:
            self._memory.pop(digest, None)

    @staticmethod
    def _valid_blob(blob: bytes) -> bool:
        if not blob or len(blob) % 4:
            return False
        try:
            import numpy as np

            return bool(np.isfinite(np.frombuffer(blob, dtype="<f4")).all())
        except ImportError:  # the cache helper remains usable without the optional numeric stack
            try:
                _unpack_vector(blob, len(blob) // 4)
            except (struct.error, ValueError):
                return False
            return True

    def _memory_put(self, digest: str, vector: list[float]) -> None:
        self._memory_put_blob(digest, _pack_vector(vector))

    def _delete_rows(self, digests: list[str]) -> None:
        if not digests or not self.disk_entries:
            return
        placeholders = ",".join("?" for _ in digests)
        with self._connect() as conn:
            conn.execute(f"DELETE FROM embeddings WHERE digest IN ({placeholders})", digests)

    def validate_present(self, digests: list[str]) -> set[str]:
        """Return digests with valid cached vectors, dropping corrupt rows."""
        valid: set[str] = set()
        missing_for_db: list[str] = []
        for digest in dict.fromkeys(digests):
            cached = self._memory_get(digest)
            if cached is not None and self._valid_blob(cached):
                valid.add(digest)
            else:
                if cached is not None:
                    self._memory_delete(digest)
                missing_for_db.append(digest)
        if not missing_for_db or not self.disk_entries:
            return valid

        corrupt: list[str] = []
        for start in range(0, len(missing_for_db), _PREPARE_BATCH):
            chunk = missing_for_db[start:start + _PREPARE_BATCH]
            placeholders = ",".join("?" for _ in chunk)
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT digest, dimensions, vector FROM embeddings WHERE digest IN ({placeholders})",
                    chunk,
                ).fetchall()
                now = time.time()
                conn.executemany(
                    "UPDATE embeddings SET last_accessed = ? WHERE digest = ?",
                    [(now, row[0]) for row in rows],
                )
            for digest, dimensions, blob in rows:
                packed = bytes(blob)
                if int(dimensions) <= 0 or len(packed) != int(dimensions) * 4 or not self._valid_blob(packed):
                    corrupt.append(str(digest))
                    continue
                valid.add(str(digest))
        self._delete_rows(corrupt)
        return valid

    def put_many(self, rows: list[tuple[str, list[float]]]) -> None:
        if not rows:
            return
        packed: list[tuple[str, int, bytes, float, float]] = []
        now = time.time()
        for digest, raw_vector in rows:
            vector = _clean_vector(raw_vector)
            blob = _pack_vector(vector)
            self._memory_put_blob(digest, blob)
            packed.append((digest, len(vector), blob, now, now))
        if not self.disk_entries:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings "
                "(digest, dimensions, vector, updated_at, last_accessed) VALUES (?, ?, ?, ?, ?)",
                packed,
            )
            count = int(conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
            excess = count - self.disk_entries
            if excess > 0:
                conn.execute(
                    "DELETE FROM embeddings WHERE digest IN ("
                    "SELECT digest FROM embeddings ORDER BY last_accessed, digest LIMIT ?)",
                    (excess,),
                )

    def vector_blobs_for(self, digests: list[str], batch_size: int = _VECTOR_BATCH) -> Iterable[list[bytes]]:
        """Yield packed vectors aligned with ``digests`` in bounded-size batches."""
        for start in range(0, len(digests), batch_size):
            chunk = digests[start:start + batch_size]
            blobs: list[Optional[bytes]] = [None] * len(chunk)
            db_positions: dict[str, list[int]] = {}
            for position, digest in enumerate(chunk):
                cached = self._memory_get(digest)
                if cached is None:
                    db_positions.setdefault(digest, []).append(position)
                else:
                    blobs[position] = cached
            if db_positions:
                if not self.disk_entries:
                    raise KeyError("embedding missing from memory-only cache")
                wanted = list(db_positions)
                placeholders = ",".join("?" for _ in wanted)
                with self._connect() as conn:
                    rows = conn.execute(
                        f"SELECT digest, dimensions, vector FROM embeddings WHERE digest IN ({placeholders})",
                        wanted,
                    ).fetchall()
                found: dict[str, bytes] = {}
                for digest, dimensions, blob in rows:
                    if int(dimensions) <= 0 or len(blob) != int(dimensions) * 4:
                        raise ValueError("invalid embedding cache entry")
                    packed = bytes(blob)
                    found[str(digest)] = packed
                    self._memory_put_blob(str(digest), packed)
                for digest, positions in db_positions.items():
                    blob = found.get(digest)
                    if blob is None:
                        raise KeyError("embedding missing from cache")
                    for position in positions:
                        blobs[position] = blob
            if any(blob is None for blob in blobs):
                raise KeyError("embedding missing from cache")
            yield [blob for blob in blobs if blob is not None]

    def vectors_for(self, digests: list[str], batch_size: int = _VECTOR_BATCH) -> Iterable[list[list[float]]]:
        """Yield vectors aligned with ``digests`` in bounded-size batches."""
        for blobs in self.vector_blobs_for(digests, batch_size):
            yield [_unpack_vector(blob, len(blob) // 4) for blob in blobs]


def openai_embedder() -> Optional[Embedder]:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None

    def embed(texts: list[str]) -> list[list[float]]:
        global _CLIENT
        import httpx

        if _CLIENT is None:
            _CLIENT = httpx.Client(timeout=60.0)
        response = _CLIENT.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": MODEL, "input": texts},
            timeout=60.0,
        )
        response.raise_for_status()
        rows = sorted(response.json()["data"], key=lambda row: row["index"])
        return [[float(item) for item in row["embedding"]] for row in rows]

    return embed


def prepare_embeddings(
    texts: list[str],
    embedder: Embedder,
    store: EmbeddingStore,
    produced_vectors: Optional[dict[str, list[float]]] = None,
) -> list[str]:
    """Ensure every unique text has a valid cached vector; return aligned digests."""
    digests = [digest_for(text) for text in texts]
    unique_texts = list(dict.fromkeys(texts))
    unique_digests = [digest_for(text) for text in unique_texts]
    missing: list[str] = []
    for start in range(0, len(unique_digests), _PREPARE_BATCH):
        digest_chunk = unique_digests[start:start + _PREPARE_BATCH]
        present = store.validate_present(digest_chunk)
        missing.extend(
            text for text, digest in zip(unique_texts[start:start + _PREPARE_BATCH], digest_chunk) if digest not in present
        )

    expected_dimensions: Optional[int] = None
    for start in range(0, len(missing), _EMBED_BATCH):
        batch = missing[start:start + _EMBED_BATCH]
        embedded = embedder(batch)
        if len(embedded) != len(batch):
            raise ValueError("embedder returned the wrong number of vectors")
        rows: list[tuple[str, list[float]]] = []
        for text, vector in zip(batch, embedded):
            clean = _clean_vector(vector)
            if expected_dimensions is None:
                expected_dimensions = len(clean)
            elif len(clean) != expected_dimensions:
                raise ValueError("embedder returned vectors that do not all have the same dimensions")
            digest = digest_for(text)
            rows.append((digest, clean))
            if produced_vectors is not None:
                produced_vectors[digest] = clean
        store.put_many(rows)
    return digests


def cached_embeddings(
    texts: list[str],
    embedder: Embedder,
    directory: Optional[Path] = None,
    *,
    store: Optional[EmbeddingStore] = None,
) -> list[list[float]]:
    if not texts:
        return []
    cache = store or EmbeddingStore(directory)
    produced: dict[str, list[float]] = {}
    digests = prepare_embeddings(texts, embedder, cache, produced)
    vectors: list[list[float]] = []
    try:
        for batch in cache.vectors_for(digests):
            vectors.extend(batch)
    except KeyError:
        # Tiny owner-configured memory/disk bounds can evict a vector before the
        # current caller reads it back. Correctness must not depend on cache capacity:
        # fall back to the vectors just embedded, or re-embed a present row displaced
        # by a later batch, without weakening the cache bounds.
        vectors = []
        text_by_digest = {}
        for text, digest in zip(texts, digests):
            text_by_digest.setdefault(digest, text)
        for digest in digests:
            try:
                vector = next(cache.vectors_for([digest], batch_size=1))[0]
            except KeyError:
                vector = produced.get(digest)
                if vector is None:
                    embedded = embedder([text_by_digest[digest]])
                    if len(embedded) != 1:
                        raise ValueError("embedder returned the wrong number of vectors")
                    vector = _clean_vector(embedded[0])
            vectors.append(vector)
    if len(vectors) != len(texts):
        raise RuntimeError("embedding cache did not produce every requested vector")
    dimensions = len(vectors[0])
    if any(len(vector) != dimensions for vector in vectors):
        raise ValueError("cached vectors do not all have the same dimensions")
    return vectors
