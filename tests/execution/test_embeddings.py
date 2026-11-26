import sqlite3
import threading

import pytest

from server.services.execution.embeddings import EmbeddingStore, cached_embeddings, digest_for


def fake_vectors(texts, dimensions=3):
    return [[float(len(text) % 7), float(index), 1.0][:dimensions] for index, text in enumerate(texts)]


def test_cache_reuse_duplicates_unicode_and_compact_database(tmp_path):
    calls = []

    def embedder(texts):
        calls.append(list(texts))
        return fake_vectors(texts)

    texts = ["hello", "hello", "unicode café 東京 🚀"]
    first = cached_embeddings(texts, embedder, tmp_path)
    second = cached_embeddings(texts, embedder, tmp_path)
    assert first == second
    assert len(calls) == 1
    assert calls[0] == ["hello", "unicode café 東京 🚀"]
    assert (tmp_path / "embeddings.sqlite3").is_file()
    assert not list(tmp_path.glob("*.json"))


def test_corrupt_cache_row_is_reembedded(tmp_path):
    store = EmbeddingStore(tmp_path)
    text = "corrupt me"
    cached_embeddings([text], lambda texts: fake_vectors(texts), tmp_path)
    digest = digest_for(text)
    with sqlite3.connect(tmp_path / "embeddings.sqlite3") as conn:
        conn.execute("UPDATE embeddings SET vector = ? WHERE digest = ?", (b"bad", digest))
    calls = []
    vectors = cached_embeddings([text], lambda texts: calls.append(list(texts)) or fake_vectors(texts), tmp_path)
    assert len(vectors) == 1
    assert calls == [[text]]


def test_wrong_dimensional_embedder_output_is_rejected(tmp_path):
    def broken(texts):
        return [[1.0, 2.0] if index == 0 else [1.0, 2.0, 3.0] for index, _ in enumerate(texts)]

    with pytest.raises(ValueError, match="same dimensions"):
        cached_embeddings(["one", "two"], broken, tmp_path)


def test_empty_nonfinite_and_wrong_count_vectors_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="wrong number"):
        cached_embeddings(["one"], lambda texts: [], tmp_path)
    with pytest.raises(ValueError, match="non-empty"):
        cached_embeddings(["one"], lambda texts: [[]], tmp_path)
    with pytest.raises(ValueError, match="finite"):
        cached_embeddings(["one"], lambda texts: [[float("nan")]], tmp_path)


def test_memory_and_disk_caches_are_bounded(tmp_path):
    store = EmbeddingStore(tmp_path, memory_entries=2, disk_entries=2)
    texts = ["a", "b", "c"]
    cached_embeddings(texts, lambda batch: fake_vectors(batch), tmp_path, store=store)
    assert store.memory_size <= 2
    assert store.memory_bytes <= 2 * 3 * 4
    with sqlite3.connect(tmp_path / "embeddings.sqlite3") as conn:
        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    assert count <= 2


def test_concurrent_cache_use_does_not_corrupt_store(tmp_path):
    errors = []

    def worker():
        try:
            cached_embeddings(["same text", "other text"], lambda texts: fake_vectors(texts), tmp_path)
        except Exception as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(cached_embeddings(["same text"], lambda texts: fake_vectors(texts), tmp_path)) == 1


def test_nonfinite_cache_blob_is_reembedded(tmp_path):
    text = "nonfinite corrupt row"
    cached_embeddings([text], lambda texts: fake_vectors(texts), tmp_path)
    digest = digest_for(text)
    with sqlite3.connect(tmp_path / "embeddings.sqlite3") as conn:
        conn.execute("UPDATE embeddings SET vector = ? WHERE digest = ?", (sqlite3.Binary(b"\x00\x00\xc0\x7f" * 3), digest))
    calls = []
    vectors = cached_embeddings([text], lambda texts: calls.append(list(texts)) or fake_vectors(texts), tmp_path)
    assert len(vectors) == 1
    assert calls == [[text]]


def test_partial_embedder_failure_reuses_committed_batches(tmp_path):
    texts = [f"partial text {index}" for index in range(129)]
    calls = []

    def flaky_embedder(batch):
        calls.append(list(batch))
        if len(calls) == 2:
            raise RuntimeError("provider stopped after the first batch")
        return fake_vectors(batch)

    with pytest.raises(RuntimeError, match="provider stopped"):
        cached_embeddings(texts, flaky_embedder, tmp_path)
    assert calls == [texts[:128], texts[128:]]

    calls.clear()
    vectors = cached_embeddings(texts, lambda batch: calls.append(list(batch)) or fake_vectors(batch), tmp_path)
    assert len(vectors) == len(texts)
    assert calls == [texts[128:]]


def test_openai_embedder_sorts_out_of_order_rows_without_network(monkeypatch):
    import httpx
    import server.services.execution.embeddings as embeddings

    requests = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ]}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def post(self, url, headers, json, timeout):
            requests.append((url, headers, json, timeout))
            return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "Client", FakeClient)
    embeddings._CLIENT = None
    try:
        embedder = embeddings.openai_embedder()
        assert embedder is not None
        assert embedder(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]
    finally:
        embeddings._CLIENT = None
    assert requests[0][2] == {"model": embeddings.MODEL, "input": ["first", "second"]}


def test_vectors_just_embedded_still_return_when_cache_bounds_evict_them(tmp_path):
    store = EmbeddingStore(tmp_path, memory_entries=2, disk_entries=2)
    vectors = cached_embeddings(["c", "a", "b"], lambda batch: fake_vectors(batch), tmp_path, store=store)
    assert vectors == fake_vectors(["c", "a", "b"])
    assert store.memory_size <= 2
