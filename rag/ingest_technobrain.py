"""
TechnoBrain public website → Qdrant incremental knowledge-sync pipeline.

Crawls https://www.technobraingroup.com/ without login (public site).
Stores content in the same Qdrant collection as Salesmate data,
tagged with source="technobrain_web".

Cache layout:
  data/technobrain_cache/
  ├── index.json          ← one entry per URL: hash, title, timestamps, vector count
  └── pages/
      └── <md5(url)>.json ← full content + metadata, readable by humans

Run manually:
    python -m rag.ingest_technobrain

Triggered via POST /trigger/ingest-technobrain from the running Nexus server.
Scheduled to run daily at 01:00 UTC via APScheduler in main.py.
"""
import os, sys, time, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from services.qdrant_store import vector_store
from rag.ingest_salesmate import chunk_text, _content_hash, _url_slug, _now_iso

# ── Cache paths ────────────────────────────────────────────────────────────────

_NEXUS_ROOT = Path(__file__).parent.parent
CACHE_DIR   = _NEXUS_ROOT / "data" / "technobrain_cache"
CACHE_INDEX = CACHE_DIR / "index.json"
PAGES_DIR   = CACHE_DIR / "pages"

# ── Domains accepted during crawl ─────────────────────────────────────────────

_ALLOWED_DOMAINS = {"www.technobraingroup.com", "technobraingroup.com"}

# ── URL skip patterns ──────────────────────────────────────────────────────────

_SKIP_URL_PATTERNS = (
    "logout", "javascript:", "mailto:", "tel:", "#",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".css", ".js", ".woff", ".ttf", ".xml", ".rss",
    "/feed/", "/wp-json/", "/wp-admin/", "/wp-login",
    "/cart/", "/checkout/", "/my-account/",
    "?replytocom=", "?share=", "?like=",
)

_SKIP_PATH_PATTERNS = (
    "/wp-content/", "/wp-includes/",
    "/tag/", "/author/", "/attachment/",
    "/xmlrpc.php",
)

# ── Content selectors (ordered by specificity) ─────────────────────────────────

_CONTENT_SELECTORS = [
    "main",
    ".entry-content",
    ".page-content",
    ".post-content",
    "article",
    ".content-area",
    ".site-content",
    ".container",
    "[class*='content']",
]


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache_index() -> dict:
    if CACHE_INDEX.exists():
        try:
            return json.loads(CACHE_INDEX.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache_index(index: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_INDEX)


def _save_page_cache(url: str, title: str, content: str, chash: str, updated: bool) -> None:
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    now = _now_iso()
    page_file = PAGES_DIR / f"{_url_slug(url)}.json"
    existing: dict = {}
    if page_file.exists():
        try:
            existing = json.loads(page_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    data = {
        "url":          url,
        "title":        title,
        "content":      content,
        "hash":         chash,
        "last_crawled": now,
        "last_updated": now if updated else existing.get("last_updated", now),
    }
    page_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Link discovery ────────────────────────────────────────────────────────────

def _discover_links(page, base_domain: str) -> list[str]:
    """Return internal page links found on the current page."""
    try:
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    except Exception:
        return []

    links = []
    for href in hrefs:
        parsed = urlparse(href)

        # Only follow links within the TechnoBrain main domain
        if parsed.netloc not in _ALLOWED_DOMAINS:
            continue

        # Skip by URL pattern
        if any(pat in href.lower() for pat in _SKIP_URL_PATTERNS):
            continue

        # Skip by path pattern
        path_lower = parsed.path.lower()
        if any(pat in path_lower for pat in _SKIP_PATH_PATTERNS):
            continue

        # Skip URLs with query strings — corporate pages use clean URLs
        if parsed.query:
            continue

        # Only include clean directory-style paths (no file extensions for media)
        last_seg = path_lower.rsplit("/", 1)[-1]
        if "." in last_seg and not last_seg.endswith((".php", ".html", ".htm")):
            continue

        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        links.append(clean)

    return list(dict.fromkeys(links))


# ── Page text extraction ───────────────────────────────────────────────────────

def _page_text(page) -> str:
    """Return visible text from the best content container on the page."""
    for sel in _CONTENT_SELECTORS:
        try:
            locs = page.locator(sel).all()
            if locs:
                texts = [l.inner_text() for l in locs if len(l.inner_text().strip()) > 80]
                if texts:
                    return "\n\n".join(texts)
        except Exception:
            continue
    try:
        return page.locator("body").inner_text()
    except Exception:
        return ""


# ── Sync helpers ───────────────────────────────────────────────────────────────

def _sync_page_content(
    url: str,
    title: str,
    content: str,
    stats: dict,
    cache_index: dict,
) -> None:
    """Compare hash, delete stale vectors if changed, upsert new chunks."""
    chash  = _content_hash(content)
    cached = cache_index.get(url, {})

    if cached.get("hash") == chash:
        cached["last_crawled"] = _now_iso()
        cache_index[url] = cached
        _save_page_cache(url, cached.get("title", title), content, chash, updated=False)
        stats["skipped"] += 1
        print(f"  → '{title}' — unchanged (skipped)")
        return

    if cached:
        vector_store.delete_by_url(url)
        stats["updated"] += 1
        label = "updated"
    else:
        stats["new"] += 1
        label = "new"

    chunks = chunk_text(content)
    if chunks:
        pid   = hashlib.md5(url.encode()).hexdigest()[:12]
        metas = [{
            "source":      "technobrain_web",
            "object_type": "page",
            "object_id":   pid,
            "object_name": title,
            "url":         url,
        } for _ in chunks]
        vector_store.upsert(chunks, metas)
        stats["chunks_added"] += len(chunks)
        print(f"  → '{title}' — {len(chunks)} chunks ({label})")

    now = _now_iso()
    cache_index[url] = {
        "hash":         chash,
        "title":        title,
        "last_crawled": now,
        "last_updated": now,
        "vector_count": len(chunks) if chunks else 0,
        "type":         "page",
    }
    _save_page_cache(url, title, content, chash, updated=True)


def _scrape_one_page(
    page,
    url: str,
    base_domain: str,
    visited: set,
    to_visit: list,
    stats: dict,
    cache_index: dict,
) -> None:
    print(f"[Page] {url}")
    try:
        page.goto(url, timeout=25_000, wait_until="networkidle")
    except Exception:
        page.goto(url, timeout=25_000, wait_until="load")
    time.sleep(0.8)

    text = _page_text(page)
    if text and len(text.strip()) > 100:
        title   = page.title() or url
        content = f"Page: {title}\nURL: {url}\n\n{text}"
        _sync_page_content(url, title, content, stats, cache_index)

    new_links = _discover_links(page, base_domain)
    for lnk in new_links:
        if lnk not in visited and lnk not in to_visit:
            to_visit.append(lnk)


def _crawl_all_pages(
    page,
    base_url: str,
    base_domain: str,
    stats: dict,
    cache_index: dict,
) -> None:
    """BFS over all internal pages of the TechnoBrain website."""
    visited:  set[str]  = set()
    to_visit: list[str] = [base_url]

    while to_visit:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            _scrape_one_page(page, url, base_domain, visited, to_visit, stats, cache_index)
        except Exception as exc:
            print(f"  ✗ {url}: {exc}")


# ── Public entry point ─────────────────────────────────────────────────────────

def run_incremental_ingest() -> None:
    """
    Incremental sync: only re-embed pages whose content hash has changed.
    Crawls the public TechnoBrain website — no login required.
    Safe to call as often as needed.
    """
    from playwright.sync_api import sync_playwright

    base_url    = "https://www.technobraingroup.com"
    base_domain = urlparse(base_url).netloc  # "www.technobraingroup.com"

    print("\n=== Nexus TechnoBrain Website Knowledge Sync ===\n")
    vector_store.ensure_collection()

    cache_index = _load_cache_index()
    stats: dict = {"new": 0, "updated": 0, "skipped": 0, "chunks_added": 0}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        _crawl_all_pages(page, base_url, base_domain, stats, cache_index)

        browser.close()

    _save_cache_index(cache_index)

    count = vector_store.count()
    print(
        f"\n✓ TechnoBrain sync complete — "
        f"{stats['new']} new | {stats['updated']} updated | {stats['skipped']} skipped"
        f" | +{stats['chunks_added']} chunks | {count} total vectors in Qdrant"
    )
    print(f"  Cache: {CACHE_INDEX}\n")


if __name__ == "__main__":
    run_incremental_ingest()
