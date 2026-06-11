"""
Tests for the incremental TechnoBrain website ingestion pipeline.

Run:
    cd nexus && python -m pytest rag/test_ingest_technobrain.py -v
"""
import json, sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Cache path patching helpers ────────────────────────────────────────────────

def _patch_cache_paths(mod, tmp_path):
    mod.CACHE_DIR   = tmp_path
    mod.CACHE_INDEX = tmp_path / "index.json"
    mod.PAGES_DIR   = tmp_path / "pages"


def _restore_cache_paths(mod, orig):
    mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR = orig


# ── cache I/O ─────────────────────────────────────────────────────────────────

def test_cache_index_roundtrip(tmp_path):
    from rag import ingest_technobrain as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        index = {"https://www.technobraingroup.com/about/": {"hash": "abc123", "title": "About"}}
        mod._save_cache_index(index)
        assert mod._load_cache_index() == index
    finally:
        _restore_cache_paths(mod, orig)


def test_cache_index_missing_returns_empty(tmp_path):
    from rag import ingest_technobrain as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        assert mod._load_cache_index() == {}
    finally:
        _restore_cache_paths(mod, orig)


def test_cache_index_corrupted_returns_empty(tmp_path):
    from rag import ingest_technobrain as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        (tmp_path / "index.json").write_text("not valid json")
        assert mod._load_cache_index() == {}
    finally:
        _restore_cache_paths(mod, orig)


def test_save_page_cache_creates_file(tmp_path):
    from rag import ingest_technobrain as mod
    from rag.ingest_salesmate import _url_slug
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        url = "https://www.technobraingroup.com/services/"
        mod._save_page_cache(url, "Services", "some content", "hashval", updated=True)

        page_file = tmp_path / "pages" / f"{_url_slug(url)}.json"
        assert page_file.exists()
        data = json.loads(page_file.read_text())
        assert data["url"]     == url
        assert data["title"]   == "Services"
        assert data["content"] == "some content"
        assert data["hash"]    == "hashval"
        assert "last_crawled"  in data
        assert "last_updated"  in data
    finally:
        _restore_cache_paths(mod, orig)


def test_save_page_cache_preserves_last_updated_when_not_updated(tmp_path):
    from rag import ingest_technobrain as mod
    from rag.ingest_salesmate import _url_slug
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        url = "https://www.technobraingroup.com/stable/"
        mod._save_page_cache(url, "Stable", "content", "hash1", updated=True)
        first = json.loads((tmp_path / "pages" / f"{_url_slug(url)}.json").read_text())

        mod._save_page_cache(url, "Stable", "content", "hash1", updated=False)
        second = json.loads((tmp_path / "pages" / f"{_url_slug(url)}.json").read_text())

        assert second["last_updated"] == first["last_updated"]
    finally:
        _restore_cache_paths(mod, orig)


# ── _sync_page_content: skip / update / new ───────────────────────────────────

LONG_CONTENT = "word " * 120  # >100 chars so the content guard passes


def test_sync_skips_unchanged_page(tmp_path):
    from rag import ingest_technobrain as mod
    from rag.ingest_salesmate import _content_hash
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        chash = _content_hash(LONG_CONTENT)
        cache_index = {"https://www.technobraingroup.com/about/": {"hash": chash, "title": "About"}}
        stats = {"new": 0, "updated": 0, "skipped": 0, "chunks_added": 0}

        with patch.object(mod.vector_store, "delete_by_url") as mock_del, \
             patch.object(mod.vector_store, "upsert") as mock_ups:
            mod._sync_page_content(
                "https://www.technobraingroup.com/about/", "About", LONG_CONTENT, stats, cache_index
            )

        mock_del.assert_not_called()
        mock_ups.assert_not_called()
        assert stats["skipped"] == 1
        assert stats["new"] == 0
        assert stats["updated"] == 0
    finally:
        _restore_cache_paths(mod, orig)


def test_sync_updates_changed_page(tmp_path):
    from rag import ingest_technobrain as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        cache_index = {"https://www.technobraingroup.com/about/": {"hash": "stale_hash", "title": "About"}}
        stats = {"new": 0, "updated": 0, "skipped": 0, "chunks_added": 0}

        with patch.object(mod.vector_store, "delete_by_url") as mock_del, \
             patch.object(mod.vector_store, "upsert") as mock_ups:
            mod._sync_page_content(
                "https://www.technobraingroup.com/about/", "About", LONG_CONTENT, stats, cache_index
            )

        mock_del.assert_called_once_with("https://www.technobraingroup.com/about/")
        mock_ups.assert_called_once()
        assert stats["updated"] == 1
        assert stats["skipped"] == 0
        assert stats["chunks_added"] > 0
    finally:
        _restore_cache_paths(mod, orig)


def test_sync_ingests_new_page(tmp_path):
    from rag import ingest_technobrain as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        cache_index: dict = {}
        stats = {"new": 0, "updated": 0, "skipped": 0, "chunks_added": 0}

        with patch.object(mod.vector_store, "delete_by_url") as mock_del, \
             patch.object(mod.vector_store, "upsert") as mock_ups:
            mod._sync_page_content(
                "https://www.technobraingroup.com/services/", "Services", LONG_CONTENT, stats, cache_index
            )

        mock_del.assert_not_called()
        mock_ups.assert_called_once()
        assert stats["new"] == 1
        assert stats["updated"] == 0
    finally:
        _restore_cache_paths(mod, orig)


def test_sync_writes_cache_entry_for_new_page(tmp_path):
    from rag import ingest_technobrain as mod
    from rag.ingest_salesmate import _content_hash
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        url = "https://www.technobraingroup.com/solutions/"
        cache_index: dict = {}
        stats = {"new": 0, "updated": 0, "skipped": 0, "chunks_added": 0}

        with patch.object(mod.vector_store, "delete_by_url"), \
             patch.object(mod.vector_store, "upsert"):
            mod._sync_page_content(url, "Solutions", LONG_CONTENT, stats, cache_index)

        assert url in cache_index
        assert cache_index[url]["hash"] == _content_hash(LONG_CONTENT)
        assert cache_index[url]["type"] == "page"
        assert cache_index[url]["vector_count"] > 0
    finally:
        _restore_cache_paths(mod, orig)


def test_sync_metadata_uses_technobrain_source(tmp_path):
    """Verify chunks are tagged source=technobrain_web (not salesmate_web)."""
    from rag import ingest_technobrain as mod
    orig = (mod.CACHE_DIR, mod.CACHE_INDEX, mod.PAGES_DIR)
    _patch_cache_paths(mod, tmp_path)
    try:
        url = "https://www.technobraingroup.com/about/"
        cache_index: dict = {}
        stats = {"new": 0, "updated": 0, "skipped": 0, "chunks_added": 0}
        captured_metas = []

        def capture_upsert(texts, metas):
            captured_metas.extend(metas)

        with patch.object(mod.vector_store, "delete_by_url"), \
             patch.object(mod.vector_store, "upsert", side_effect=capture_upsert):
            mod._sync_page_content(url, "About", LONG_CONTENT, stats, cache_index)

        assert captured_metas, "upsert should have been called with metadata"
        for meta in captured_metas:
            assert meta["source"] == "technobrain_web"
            assert meta["object_type"] == "page"
            assert meta["url"] == url
    finally:
        _restore_cache_paths(mod, orig)


# ── _discover_links filtering ─────────────────────────────────────────────────

def _make_mock_page(hrefs: list[str]) -> MagicMock:
    mock = MagicMock()
    mock.eval_on_selector_all.return_value = hrefs
    return mock


def test_discover_links_accepts_internal_links():
    from rag.ingest_technobrain import _discover_links
    page = _make_mock_page([
        "https://www.technobraingroup.com/about/",
        "https://www.technobraingroup.com/services/",
        "https://technobraingroup.com/contact/",
    ])
    links = _discover_links(page, "www.technobraingroup.com")
    assert "https://www.technobraingroup.com/about/" in links
    assert "https://www.technobraingroup.com/services/" in links
    assert "https://technobraingroup.com/contact/" in links


def test_discover_links_rejects_external_links():
    from rag.ingest_technobrain import _discover_links
    page = _make_mock_page([
        "https://google.com/",
        "https://salesmate.technobraingroup.com/",
        "https://facebook.com/technobraingroup",
        "https://linkedin.com/company/technobrain",
    ])
    links = _discover_links(page, "www.technobraingroup.com")
    assert links == []


def test_discover_links_skips_media_and_wp_paths():
    from rag.ingest_technobrain import _discover_links
    page = _make_mock_page([
        "https://www.technobraingroup.com/wp-content/uploads/logo.png",
        "https://www.technobraingroup.com/wp-admin/",
        "https://www.technobraingroup.com/feed/",
        "https://www.technobraingroup.com/wp-json/wp/v2/posts",
        "https://www.technobraingroup.com/tag/africa/",
        "https://www.technobraingroup.com/author/admin/",
    ])
    links = _discover_links(page, "www.technobraingroup.com")
    assert links == []


def test_discover_links_skips_query_strings():
    from rag.ingest_technobrain import _discover_links
    page = _make_mock_page([
        "https://www.technobraingroup.com/search/?s=ai",
        "https://www.technobraingroup.com/?cat=5",
        "https://www.technobraingroup.com/about/?replytocom=42",
    ])
    links = _discover_links(page, "www.technobraingroup.com")
    assert links == []


def test_discover_links_deduplicates():
    from rag.ingest_technobrain import _discover_links
    page = _make_mock_page([
        "https://www.technobraingroup.com/about/",
        "https://www.technobraingroup.com/about/",
        "https://www.technobraingroup.com/about/",
    ])
    links = _discover_links(page, "www.technobraingroup.com")
    assert links.count("https://www.technobraingroup.com/about/") == 1


def test_discover_links_handles_eval_error_gracefully():
    from rag.ingest_technobrain import _discover_links
    page = MagicMock()
    page.eval_on_selector_all.side_effect = Exception("JS error")
    links = _discover_links(page, "www.technobraingroup.com")
    assert links == []
