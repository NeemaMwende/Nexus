"""
Tests for the incremental Salesmate ingestion pipeline.

Run:
    cd nexus && python -m pytest rag/test_ingest_salesmate.py -v
"""
import json, sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── chunk_text ────────────────────────────────────────────────────────────────

def test_chunk_text_produces_multiple_chunks():
    from rag.ingest_salesmate import chunk_text
    text = " ".join(["word"] * 500)
    chunks = chunk_text(text, chunk_size=400, overlap=50)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.split()) <= 400


def test_chunk_text_empty_returns_empty():
    from rag.ingest_salesmate import chunk_text
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_short_text_is_single_chunk():
    from rag.ingest_salesmate import chunk_text
    chunks = chunk_text("hello world this is a test")
    assert len(chunks) == 1
    assert chunks[0] == "hello world this is a test"


def test_chunk_text_overlap_shares_words():
    from rag.ingest_salesmate import chunk_text
    text = " ".join([str(i) for i in range(500)])
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    # First word of chunk[1] should appear near the end of chunk[0]
    last_words_of_first = set(chunks[0].split()[-20:])
    first_words_of_second = set(chunks[1].split()[:20])
    assert last_words_of_first & first_words_of_second  # overlap is non-empty


# ── hash helpers ──────────────────────────────────────────────────────────────

def test_content_hash_is_deterministic():
    from rag.ingest_salesmate import _content_hash
    assert _content_hash("hello") == _content_hash("hello")


def test_content_hash_differs_for_different_content():
    from rag.ingest_salesmate import _content_hash
    assert _content_hash("hello") != _content_hash("world")


def test_url_slug_is_deterministic():
    from rag.ingest_salesmate import _url_slug
    url = "https://salesmate.example.com/page"
    assert _url_slug(url) == _url_slug(url)


def test_url_slug_differs_for_different_urls():
    from rag.ingest_salesmate import _url_slug
    assert _url_slug("https://example.com/a") != _url_slug("https://example.com/b")


def test_url_slug_is_hex_string():
    from rag.ingest_salesmate import _url_slug
    slug = _url_slug("https://example.com")
    assert len(slug) == 32
    assert all(c in "0123456789abcdef" for c in slug)


# ── cache I/O ─────────────────────────────────────────────────────────────────

def _patch_cache_paths(mod, tmp_path):
    mod.CACHE_DIR   = tmp_path
    mod.CACHE_INDEX = tmp_path / "index.json"
    mod.PAGES_DIR   = tmp_path / "pages"


def _restore_cache_paths(mod, orig):
    mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR = orig


def test_cache_index_roundtrip(tmp_path):
    from rag import ingest_salesmate as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        index = {"https://example.com/page": {"hash": "abc123", "title": "Page"}}
        mod._save_cache_index(index)
        assert mod._load_cache_index() == index
    finally:
        _restore_cache_paths(mod, orig)


def test_cache_index_missing_returns_empty(tmp_path):
    from rag import ingest_salesmate as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        assert mod._load_cache_index() == {}
    finally:
        _restore_cache_paths(mod, orig)


def test_cache_index_corrupted_returns_empty(tmp_path):
    from rag import ingest_salesmate as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        (tmp_path / "index.json").write_text("not valid json")
        assert mod._load_cache_index() == {}
    finally:
        _restore_cache_paths(mod, orig)


def test_save_page_cache_creates_file(tmp_path):
    from rag import ingest_salesmate as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        url = "https://example.com/test-page"
        mod._save_page_cache(url, "Test Page", "some content", "hashval", updated=True)

        page_file = tmp_path / "pages" / f"{mod._url_slug(url)}.json"
        assert page_file.exists()
        data = json.loads(page_file.read_text())
        assert data["url"]     == url
        assert data["title"]   == "Test Page"
        assert data["content"] == "some content"
        assert data["hash"]    == "hashval"
        assert "last_crawled"  in data
        assert "last_updated"  in data
    finally:
        _restore_cache_paths(mod, orig)


def test_save_page_cache_preserves_last_updated_when_not_updated(tmp_path):
    from rag import ingest_salesmate as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        url = "https://example.com/stable"
        # First save (updated=True) sets last_updated
        mod._save_page_cache(url, "Stable", "content", "hash1", updated=True)
        first = json.loads((tmp_path / "pages" / f"{mod._url_slug(url)}.json").read_text())

        # Second save (updated=False) should keep last_updated from first write
        mod._save_page_cache(url, "Stable", "content", "hash1", updated=False)
        second = json.loads((tmp_path / "pages" / f"{mod._url_slug(url)}.json").read_text())

        assert second["last_updated"] == first["last_updated"]
    finally:
        _restore_cache_paths(mod, orig)


# ── _sync_page_content: skip / update / new ───────────────────────────────────

LONG_CONTENT = "word " * 120  # >100 chars so the if-guard in _scrape_one_page passes


def test_sync_skips_unchanged_page(tmp_path):
    """Unchanged hash → delete_by_url and upsert must NOT be called."""
    from rag import ingest_salesmate as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        chash = mod._content_hash(LONG_CONTENT)
        cache_index = {"https://example.com/page": {"hash": chash, "title": "Page"}}
        stats = {"new": 0, "updated": 0, "skipped": 0, "chunks_added": 0}

        with patch.object(mod.vector_store, "delete_by_url") as mock_del, \
             patch.object(mod.vector_store, "upsert") as mock_ups:
            mod._sync_page_content("https://example.com/page", "Page", LONG_CONTENT, stats, cache_index)

        mock_del.assert_not_called()
        mock_ups.assert_not_called()
        assert stats["skipped"] == 1
        assert stats["new"] == 0
        assert stats["updated"] == 0
    finally:
        _restore_cache_paths(mod, orig)


def test_sync_updates_changed_page(tmp_path):
    """Changed hash → delete old vectors then upsert new chunks."""
    from rag import ingest_salesmate as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        cache_index = {"https://example.com/page": {"hash": "stale_hash", "title": "Page"}}
        stats = {"new": 0, "updated": 0, "skipped": 0, "chunks_added": 0}

        with patch.object(mod.vector_store, "delete_by_url") as mock_del, \
             patch.object(mod.vector_store, "upsert") as mock_ups:
            mod._sync_page_content("https://example.com/page", "Page", LONG_CONTENT, stats, cache_index)

        mock_del.assert_called_once_with("https://example.com/page")
        mock_ups.assert_called_once()
        assert stats["updated"] == 1
        assert stats["skipped"] == 0
        assert stats["chunks_added"] > 0
    finally:
        _restore_cache_paths(mod, orig)


def test_sync_ingests_new_page(tmp_path):
    """Page not in cache → upsert without prior delete."""
    from rag import ingest_salesmate as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        cache_index: dict = {}
        stats = {"new": 0, "updated": 0, "skipped": 0, "chunks_added": 0}

        with patch.object(mod.vector_store, "delete_by_url") as mock_del, \
             patch.object(mod.vector_store, "upsert") as mock_ups:
            mod._sync_page_content("https://example.com/new", "New Page", LONG_CONTENT, stats, cache_index)

        mock_del.assert_not_called()
        mock_ups.assert_called_once()
        assert stats["new"] == 1
        assert stats["updated"] == 0
    finally:
        _restore_cache_paths(mod, orig)


def test_sync_writes_cache_entry_for_new_page(tmp_path):
    """After ingesting a new page the cache_index should contain an entry."""
    from rag import ingest_salesmate as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        url = "https://example.com/new"
        cache_index: dict = {}
        stats = {"new": 0, "updated": 0, "skipped": 0, "chunks_added": 0}

        with patch.object(mod.vector_store, "delete_by_url"), \
             patch.object(mod.vector_store, "upsert"):
            mod._sync_page_content(url, "New Page", LONG_CONTENT, stats, cache_index)

        assert url in cache_index
        assert cache_index[url]["hash"] == mod._content_hash(LONG_CONTENT)
        assert cache_index[url]["type"] == "page"
    finally:
        _restore_cache_paths(mod, orig)
