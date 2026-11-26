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
        self._memory: OrderedDict[str, list[float]] = OrderedDict()
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

    def _memory_get(self, digest: str) -> Optional[list[float]]:
        with self._memory_lock:
            vector = self._memory.get(digest)
            if vector is not None:
                self._memory.move_to_end(digest)
            return vector

    def _memory_put(self, digest: str, vector: list[float]) -> None:
        if self.memory_entries == 0:
            return
        with self._memory_lock:
            self._memory[digest] = list(vector)
            self._memory.move_to_end(digest)
            while len(self._memory) > self.memory_entries:
                self._memory.popitem(last=False)

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
            if self._memory_get(digest) is not None:
                valid.add(digest)
            else:
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
                try:
                    vector = _unpack_vector(blob, int(dimensions))
                except (struct.error, TypeError, ValueError):
                    corrupt.append(str(digest))
                    continue
                self._memory_put(str(digest), vector)
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
            self._memory_put(digest, vector)
            packed.append((digest, len(vector), _pack_vector(vector), now, now))
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

    def vectors_for(self, digests: list[str], batch_size: int = _VECTOR_BATCH) -> Iterable[list[list[float]]]:
        """Yield vectors aligned with ``digests`` in bounded-size batches."""
        for start in range(0, len(digests), batch_size):
            chunk = digests[start:start + batch_size]
            vectors: list[Optional[list[float]]] = [None] * len(chunk)
            db_positions: dict[str, list[int]] = {}
            for position, digest in enumerate(chunk):
                cached = self._memory_get(digest)
                if cached is None:
                    db_positions.setdefault(digest, []).append(position)
                else:
                    vectors[position] = cached
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
                found: dict[str, list[float]] = {}
                for digest, dimensions, blob in rows:
                    vector = _unpack_vector(blob, int(dimensions))
                    found[str(digest)] = vector
                    self._memory_put(str(digest), vector)
                for digest, positions in db_positions.items():
                    vector = found.get(digest)
                    if vector is None:
                        raise KeyError("embedding missing from cache")
                    for position in positions:
                        vectors[position] = vector
            if any(vector is None for vector in vectors):
                raise KeyError("embedding missing from cache")
            yield [vector for vector in vectors if vector is not None]


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
            rows.append((digest_for(text), clean))
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
    digests = prepare_embeddings(texts, embedder, cache)
    vectors: list[list[float]] = []
    for batch in cache.vectors_for(digests):
        vectors.extend(batch)
    if len(vectors) != len(texts):
        raise RuntimeError("embedding cache did not produce every requested vector")
    dimensions = len(vectors[0])
    if any(len(vector) != dimensions for vector in vectors):
        raise ValueError("cached vectors do not all have the same dimensions")
    return vectors
