"""
Salesmate → Qdrant ingestion pipeline.

Logs in to the custom Salesmate instance at SALESMATE_URL using
SALESMATE_USERNAME / SALESMATE_PASSWORD, crawls every internal page
with a headless Playwright browser, extracts visible text, and upserts
chunks to Qdrant as the agent's knowledge base.

Also downloads and parses any PDF/DOCX/TXT files linked from the site.

Run:
    python -m rag.ingest_salesmate

Or triggered via POST /trigger/ingest inside the running Nexus server.
"""
import io, os, sys, time, hashlib
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import requests as _requests
import config
from services.qdrant_store import vector_store

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

# URL path segments that identify document files even without a file extension
_DOCUMENT_PATH_SEGMENTS = ("/pdf/", "/docx/", "/doc/")
# URL path segments that identify files we should skip entirely (no text to extract)
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

    # Locate email/username field — WordPress uses name="log"; fall back to common selectors
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

    # Locate password field — WordPress uses name="pwd"
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

    # Submit the form — WordPress uses id="wp-submit"
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

    # Fallback to whole body
    try:
        return page.locator("body").inner_text()
    except Exception:
        return ""


def _discover_links(page, base_domain: str) -> tuple[list[str], list[str]]:
    """
    Return (page_links, document_links) found on the current page.
    Both lists contain absolute URLs, deduplicated, within base_domain.
    """
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

        # Skip binary/media files we can't extract text from
        if any(seg in path_lower for seg in _SKIP_FILE_SEGMENTS):
            continue

        ext = path_lower.rsplit(".", 1)[-1] if "." in path_lower else ""
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        # Classify as document if it has a known doc extension OR
        # if the URL path contains a document-type segment (e.g. /pdf/, /docx/)
        is_doc = ext in _DOCUMENT_EXTS or any(seg in path_lower for seg in _DOCUMENT_PATH_SEGMENTS)
        if is_doc:
            doc_links.append(clean)
        else:
            page_links.append(clean)

    return list(dict.fromkeys(page_links)), list(dict.fromkeys(doc_links))


# ── Document downloader ───────────────────────────────────────────────────────

def _infer_doc_ext(url: str) -> str:
    """Infer file extension from URL path, including extensionless S3-style paths."""
    path = urlparse(url).path.lower()
    # Extension present (e.g. file.pdf)
    if "." in path.rsplit("/", 1)[-1]:
        return path.rsplit(".", 1)[-1]
    # No extension — look for type-named path segment (/pdf/, /docx/, /doc/, /txt/)
    for seg in ("/pdf/", "/docx/", "/doc/", "/txt/"):
        if seg in path:
            return seg.strip("/")
    return ""


def _ingest_document(file_url: str, cookies: list[dict], total_ref: list) -> None:
    """Download a file from file_url using the browser session cookies and ingest it."""
    ext = _infer_doc_ext(file_url)
    filename = file_url.rsplit("/", 1)[-1]

    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = _requests.Session()
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

    try:
        resp = session.get(file_url, timeout=30, verify=False)
        resp.raise_for_status()
        text = _extract_file_text(resp.content, ext)
        if not text or len(text.strip()) < 50:
            return

        chunks = chunk_text(text)
        file_id = hashlib.md5(file_url.encode()).hexdigest()[:12]
        metas = [{
            "source":      "salesmate_web",
            "object_type": "document",
            "object_id":   file_id,
            "object_name": filename,
            "url":         file_url,
        } for _ in chunks]

        vector_store.upsert(chunks, metas)
        total_ref[0] += len(chunks)
        print(f"  [Doc] {filename} → {len(chunks)} chunks")
    except Exception as e:
        print(f"  [Doc] Skipped {filename}: {e}")


# ── Main crawler ──────────────────────────────────────────────────────────────

def _scrape_one_page(
    page,
    url: str,
    base_domain: str,
    visited: set,
    to_visit: list,
    doc_queue: list,
    total: list,
) -> None:
    """Navigate to url, ingest its text, and enqueue newly discovered links."""
    print(f"[Page] {url}")
    try:
        page.goto(url, timeout=25_000, wait_until="networkidle")
    except Exception:
        # Fallback for pages that never reach networkidle (long-polling, etc.)
        page.goto(url, timeout=25_000, wait_until="load")
    time.sleep(0.8)

    text = _page_text(page)
    if text and len(text.strip()) > 100:
        title   = page.title() or url
        content = f"Page: {title}\nURL: {url}\n\n{text}"
        chunks  = chunk_text(content)
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
            total[0] += len(chunks)
            print(f"  → '{title}' — {len(chunks)} chunks")

    new_pages, new_docs = _discover_links(page, base_domain)
    for lnk in new_pages:
        if lnk not in visited and lnk not in to_visit:
            to_visit.append(lnk)
    for doc in new_docs:
        if doc not in doc_queue:
            doc_queue.append(doc)


def _crawl_all_pages(page, base_url: str, base_domain: str, total: list) -> list:
    """BFS over all internal pages; return the collected document URLs."""
    visited:   set[str]  = set()
    to_visit:  list[str] = [base_url, page.url]
    doc_queue: list[str] = []

    while to_visit:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            _scrape_one_page(page, url, base_domain, visited, to_visit, doc_queue, total)
        except Exception as exc:
            print(f"  ✗ {url}: {exc}")

    return doc_queue


def run_full_ingest() -> None:
    from playwright.sync_api import sync_playwright

    base_url    = config.SALESMATE_URL.rstrip("/")
    username    = config.SALESMATE_USERNAME
    password    = config.SALESMATE_PASSWORD
    base_domain = urlparse(base_url).netloc

    if not username or not password:
        print("[Scraper] SALESMATE_USERNAME / SALESMATE_PASSWORD not set in .env — aborting.")
        return

    print("\n=== Nexus Salesmate Web Scraper ===\n")
    vector_store.ensure_collection()

    total = [0]

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

        doc_queue = _crawl_all_pages(page, base_url, base_domain, total)

        if doc_queue:
            print(f"\n[Docs] Downloading {len(doc_queue)} document(s)...")
            cookies = context.cookies()
            for doc_url in doc_queue:
                _ingest_document(doc_url, cookies, total)

        browser.close()

    count = vector_store.count()
    print(f"\n✓ Scrape complete — {total[0]} new chunks added, {count} total vectors in Qdrant\n")


if __name__ == "__main__":
    run_full_ingest()
