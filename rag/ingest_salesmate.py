"""
Salesmate → Qdrant incremental knowledge-sync pipeline.

On each run:
  1. Logs in to Salesmate via Playwright.
  2. Crawls every internal page.
  3. Computes an MD5 content hash per page/document.
  4. Skips pages whose hash hasn't changed since the last run.
  5. For changed pages: deletes stale Qdrant vectors, re-embeds, re-upserts.
  6. Writes a human-readable JSON cache to data/salesmate_cache/.

Cache layout:
  data/salesmate_cache/
  ├── index.json          ← one entry per URL: hash, title, timestamps, vector count
  └── pages/
      └── <md5(url)>.json ← full content + metadata, readable by humans

Run manually:
    python -m rag.ingest_salesmate

Triggered via POST /trigger/ingest from the running Nexus server.
Scheduled to run daily at midnight UTC via APScheduler in main.py.
"""
import io, os, sys, time, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import requests as _requests
import config
from services.qdrant_store import vector_store

# ── Cache paths ───────────────────────────────────────────────────────────────

_NEXUS_ROOT = Path(__file__).parent.parent
CACHE_DIR   = _NEXUS_ROOT / "data" / "salesmate_cache"
CACHE_INDEX = CACHE_DIR / "index.json"
PAGES_DIR   = CACHE_DIR / "pages"


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _url_slug(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


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
    """Write a per-URL JSON file with the full content — readable by humans."""
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


# ── Text chunker ──────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 400, overlap: int = 50) -> list[str]:
    if not text or not text.strip():
        return []
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


# ── File text extractor ───────────────────────────────────────────────────────

def _extract_file_text(content: bytes, ext: str) -> str:
    if ext == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n\n".join(p.extract_text() or "" for p in reader.pages)
    elif ext in ("docx", "doc"):
        from docx import Document
        doc = Document(io.BytesIO(content))
        return "\n".join(para.text for para in doc.paragraphs)
    elif ext == "txt":
        return content.decode("utf-8", errors="replace")
    return ""


# ── Playwright helpers ────────────────────────────────────────────────────────

_SKIP_URL_PATTERNS = (
    "logout", "signout", "delete", "remove", "export",
    "javascript:", "mailto:", "tel:", "#",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".css", ".js", ".woff", ".ttf",
)

_DOCUMENT_EXTS = ("pdf", "docx", "doc", "txt")

_DOCUMENT_PATH_SEGMENTS = ("/pdf/", "/docx/", "/doc/")
_SKIP_FILE_SEGMENTS = (
    "/mp4/", "/mov/", "/avi/", "/mkv/",
    "/pptx/", "/ppt/", "/xls/", "/xlsx/",
    "/jpg/", "/jpeg/", "/png/", "/gif/", "/svg/", "/tif/", "/tiff/", "/webp/",
)

_CONTENT_SELECTORS = [
    "main", ".main-content", "#main-content", ".content-area",
    ".page-content", "article", ".container", ".app-content",
    "[class*='content']", "[class*='dashboard']", "[id*='app']",
]


def _login(page, base_url: str, username: str, password: str) -> bool:
    """Navigate to the site, fill the login form, and return True on success."""
    print(f"[Scraper] Opening {base_url} ...")
    page.goto(base_url, timeout=30_000)
    page.wait_for_load_state("networkidle", timeout=20_000)

    filled_user = False
    for sel in [
        'input[name="log"]', '#user_login',
        'input[type="email"]', 'input[name="email"]',
        'input[name="username"]', 'input[name="user"]',
        'input[placeholder*="email" i]', 'input[placeholder*="user" i]',
        "#email", "#username",
    ]:
        if page.locator(sel).count() > 0:
            page.fill(sel, username)
            filled_user = True
            break

    filled_pass = False
    for sel in [
        'input[name="pwd"]', '#user_pass',
        'input[type="password"]', 'input[name="password"]',
        'input[placeholder*="password" i]', "#password",
    ]:
        if page.locator(sel).count() > 0:
            page.fill(sel, password)
            filled_pass = True
            break

    if not filled_user or not filled_pass:
        print("[Scraper] Could not find login form fields.")
        return False

    submitted = False
    for sel in [
        '#wp-submit', 'input[type="submit"]',
        'button[type="submit"]',
        'button:has-text("Login")', 'button:has-text("Log in")',
        'button:has-text("Sign in")', 'button:has-text("Submit")',
    ]:
        if page.locator(sel).count() > 0:
            page.click(sel)
            submitted = True
            break

    if not submitted:
        page.keyboard.press("Enter")

    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        pass

    current = page.url
    if any(kw in current.lower() for kw in ("/login", "/signin", "/sign-in", "/wp-login")):
        print(f"[Scraper] Login may have failed — still at: {current}")
        return False

    print(f"[Scraper] Login successful → {current}")
    return True


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


def _discover_links(page, base_domain: str) -> tuple[list[str], list[str]]:
    """Return (page_links, document_links) found on the current page."""
    try:
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    except Exception:
        return [], []

    page_links, doc_links = [], []
    for href in hrefs:
        parsed = urlparse(href)
        if parsed.netloc != base_domain:
            continue
        if any(href.lower().find(pat) != -1 for pat in _SKIP_URL_PATTERNS):
            continue

        path_lower = parsed.path.lower()

        if any(seg in path_lower for seg in _SKIP_FILE_SEGMENTS):
            continue

        ext = path_lower.rsplit(".", 1)[-1] if "." in path_lower else ""
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        is_doc = ext in _DOCUMENT_EXTS or any(seg in path_lower for seg in _DOCUMENT_PATH_SEGMENTS)
        if is_doc:
            doc_links.append(clean)
        else:
            page_links.append(clean)

    return list(dict.fromkeys(page_links)), list(dict.fromkeys(doc_links))


# ── Document downloader ───────────────────────────────────────────────────────

def _infer_doc_ext(url: str) -> str:
    path = urlparse(url).path.lower()
    if "." in path.rsplit("/", 1)[-1]:
        return path.rsplit(".", 1)[-1]
    for seg in ("/pdf/", "/docx/", "/doc/", "/txt/"):
        if seg in path:
            return seg.strip("/")
    return ""


def _ingest_document(
    file_url: str,
    cookies: list[dict],
    stats: dict,
    cache_index: dict,
) -> None:
    """Download a file, compare hash against cache, ingest only if changed."""
    ext = _infer_doc_ext(file_url)
    filename = file_url.rsplit("/", 1)[-1]

    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = _requests.Session()
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

    try:
        # verify=False: internal Salesmate server uses a self-signed certificate
        resp = session.get(file_url, timeout=30, verify=False)  # noqa: S501
        resp.raise_for_status()
        text = _extract_file_text(resp.content, ext)
        if not text or len(text.strip()) < 50:
            return

        chash  = _content_hash(text)
        cached = cache_index.get(file_url, {})

        if cached.get("hash") == chash:
            cached["last_crawled"] = _now_iso()
            cache_index[file_url] = cached
            stats["skipped"] += 1
            print(f"  [Doc] {filename} — unchanged (skipped)")
            return

        if cached:
            vector_store.delete_by_url(file_url)
            stats["updated"] += 1
            label = "updated"
        else:
            stats["new"] += 1
            label = "new"

        chunks  = chunk_text(text)
        file_id = hashlib.md5(file_url.encode()).hexdigest()[:12]
        metas   = [{
            "source":      "salesmate_web",
            "object_type": "document",
            "object_id":   file_id,
            "object_name": filename,
            "url":         file_url,
        } for _ in chunks]

        vector_store.upsert(chunks, metas)
        stats["chunks_added"] += len(chunks)

        now = _now_iso()
        cache_index[file_url] = {
            "hash":         chash,
            "title":        filename,
            "last_crawled": now,
            "last_updated": now,
            "vector_count": len(chunks),
            "type":         "document",
        }
        _save_page_cache(file_url, filename, text, chash, updated=True)
        print(f"  [Doc] {filename} → {len(chunks)} chunks ({label})")

    except Exception as e:
        print(f"  [Doc] Skipped {filename}: {e}")


# ── Main crawler ──────────────────────────────────────────────────────────────

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
            "source":      "salesmate_web",
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
    doc_queue: list,
    stats: dict,
    cache_index: dict,
) -> None:
    """Navigate to url, compare hash, upsert only if content changed."""
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

    new_pages, new_docs = _discover_links(page, base_domain)
    for lnk in new_pages:
        if lnk not in visited and lnk not in to_visit:
            to_visit.append(lnk)
    for doc in new_docs:
        if doc not in doc_queue:
            doc_queue.append(doc)


def _crawl_all_pages(
    page,
    base_url: str,
    base_domain: str,
    stats: dict,
    cache_index: dict,
) -> list:
    """BFS over all internal pages; return collected document URLs."""
    visited:   set[str]  = set()
    to_visit:  list[str] = [base_url, page.url]
    doc_queue: list[str] = []

    while to_visit:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            _scrape_one_page(page, url, base_domain, visited, to_visit, doc_queue, stats, cache_index)
        except Exception as exc:
            print(f"  ✗ {url}: {exc}")

    return doc_queue


# ── Public entry points ───────────────────────────────────────────────────────

def run_incremental_ingest() -> None:
    """
    Incremental sync: only re-embed pages whose content hash has changed.
    Safe to call as often as needed — unchanged pages are skipped entirely.
    Writes a human-readable JSON cache to data/salesmate_cache/.
    """
    from playwright.sync_api import sync_playwright

    base_url    = config.SALESMATE_URL.rstrip("/")
    username    = config.SALESMATE_USERNAME
    password    = config.SALESMATE_PASSWORD
    base_domain = urlparse(base_url).netloc

    if not username or not password:
        print("[Scraper] SALESMATE_USERNAME / SALESMATE_PASSWORD not set in .env — aborting.")
        return

    print("\n=== Nexus Salesmate Knowledge Sync ===\n")
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

        if not _login(page, base_url, username, password):
            print("[Scraper] Login failed. Check SALESMATE_USERNAME / SALESMATE_PASSWORD.")
            browser.close()
            return

        doc_queue = _crawl_all_pages(page, base_url, base_domain, stats, cache_index)

        if doc_queue:
            print(f"\n[Docs] Checking {len(doc_queue)} document(s)...")
            cookies = context.cookies()
            for doc_url in doc_queue:
                _ingest_document(doc_url, cookies, stats, cache_index)

        browser.close()

    _save_cache_index(cache_index)

    count = vector_store.count()
    print(
        f"\n✓ Sync complete — "
        f"{stats['new']} new | {stats['updated']} updated | {stats['skipped']} skipped"
        f" | +{stats['chunks_added']} chunks | {count} total vectors in Qdrant"
    )
    print(f"  Cache: {CACHE_INDEX}\n")


def run_full_ingest() -> None:
    """Backward-compatible alias — now runs incrementally."""
    run_incremental_ingest()


if __name__ == "__main__":
    run_incremental_ingest()
