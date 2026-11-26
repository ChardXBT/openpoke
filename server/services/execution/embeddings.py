"""Optional OpenAI-compatible embeddings for agent search.

Disabled unless ``OPENAI_API_KEY`` is set. Vectors are cached by model+text hash so a
repeated search over a large unchanged registry does not call the provider again.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Optional

Embedder = Callable[[list[str]], list[list[float]]]
MODEL = "text-embedding-3-small"
_CLIENT = None
_MEMORY_CACHE: dict[str, list[float]] = {}


def cache_dir() -> Path:
    override = (os.getenv("OPENPOKE_EMBEDDING_CACHE_DIR") or "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data" / "embeddings"


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


def cached_embeddings(texts: list[str], embedder: Embedder, directory: Optional[Path] = None) -> list[list[float]]:
    if not texts:
        return []
    root = directory or cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    vectors: list[Optional[list[float]]] = [None] * len(texts)
    misses: dict[str, list[int]] = {}
    paths: dict[str, Path] = {}
    for position, text in enumerate(texts):
        digest = hashlib.sha256(f"{MODEL}\0{text}".encode("utf-8")).hexdigest()
        path = root / f"{digest}.json"
        paths[text] = path
        cached = _MEMORY_CACHE.get(digest)
        if cached is not None:
            vectors[position] = cached
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list) or not all(isinstance(item, (int, float)) for item in raw):
                raise ValueError("invalid embedding cache entry")
            cached = [float(item) for item in raw]
            _MEMORY_CACHE[digest] = cached
            vectors[position] = cached
        except (OSError, json.JSONDecodeError, ValueError):
            misses.setdefault(text, []).append(position)
    missing = list(misses)
    for start in range(0, len(missing), 128):
        batch = missing[start:start + 128]
        embedded = embedder(batch)
        if len(embedded) != len(batch):
            raise ValueError("embedder returned the wrong number of vectors")
        for text, vector in zip(batch, embedded):
            clean = [float(item) for item in vector]
            paths[text].write_text(json.dumps(clean, separators=(",", ":")), encoding="utf-8")
            digest = hashlib.sha256(f"{MODEL}\0{text}".encode("utf-8")).hexdigest()
            _MEMORY_CACHE[digest] = clean
            for position in misses[text]:
                vectors[position] = clean
    if any(vector is None for vector in vectors):
        raise RuntimeError("embedding cache did not produce every requested vector")
    return [vector for vector in vectors if vector is not None]
