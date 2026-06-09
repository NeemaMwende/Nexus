"""
Salesmate → Qdrant ingestion pipeline.
Pulls Products, Notes, Deals, Files, and Knowledge Base articles
from the Salesmate REST API, chunks the text, embeds, and upserts to Qdrant.

Run:
    python -m rag.ingest_salesmate

Or scheduled daily inside Nexus via APScheduler.
"""
import os, io, sys, time, requests, traceback
from typing import Optional
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from services.qdrant_store import vector_store, embed_texts

# ── Salesmate API client ──────────────────────────────────────────────────────

SALESMATE_BASE = f"https://apis.salesmate.io"
HEADERS = {
    "accessToken": config.SALESMATE_API_KEY,
    "x-linkname":  config.SALESMATE_LINKNAME,
    "Content-Type": "application/json",
}


def sm_get(path: str, params: dict = None) -> dict:
    """GET from Salesmate API with basic retry."""
    url = f"{SALESMATE_BASE}{path}"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def sm_post(path: str, payload: dict) -> dict:
    url = f"{SALESMATE_BASE}{path}"
    r = requests.post(url, headers=HEADERS, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


# ── Text chunker ─────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 400, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks by word count."""
    if not text or not text.strip():
        return []
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


# ── Per-object ingestors ──────────────────────────────────────────────────────

def ingest_products():
    """Ingest all Products from Salesmate."""
    print("[Ingest] Fetching Products...")
    try:
        # Salesmate v4 list endpoint with pagination
        page = 1
        total_ingested = 0
        while True:
            data = sm_post("/v4/products/search", {
                "query": {"operator": "AND", "queries": []},
                "pageNo": page,
                "rows": 50,
                "sortBy": "createdAt",
                "sortOrder": "asc",
                "includeTotal": True,
            })

            products = data.get("Data", {}).get("data", [])
            if not products:
                break

            texts, metas = [], []
            for p in products:
                text = _build_product_text(p)
                for chunk in chunk_text(text):
                    texts.append(chunk)
                    metas.append({
                        "source":      "salesmate",
                        "object_type": "product",
                        "object_id":   str(p.get("id", "")),
                        "object_name": p.get("name", ""),
                    })

            if texts:
                vector_store.upsert(texts, metas)
                total_ingested += len(texts)
                print(f"  Products page {page}: {len(texts)} chunks")

            if len(products) < 50:
                break
            page += 1

        print(f"  ✓ Products done — {total_ingested} chunks total")
    except Exception as e:
        print(f"  ✗ Products failed: {e}")
        traceback.print_exc()


def _build_product_text(p: dict) -> str:
    parts = [
        f"Product: {p.get('name', '')}",
        f"Description: {p.get('description', '')}",
        f"Price: {p.get('price', '')} {p.get('currency', '')}",
        f"Unit: {p.get('unit', '')}",
        f"SKU: {p.get('sku', '')}",
        f"Category: {p.get('category', '')}",
        f"Status: {p.get('status', '')}",
    ]
    return "\n".join(p for p in parts if p.split(": ", 1)[1])


def ingest_notes():
    """Ingest all Notes from Salesmate (deal notes, contact notes etc.)."""
    print("[Ingest] Fetching Notes...")
    try:
        page = 1
        total = 0
        while True:
            data = sm_post("/v4/notes/search", {
                "query": {"operator": "AND", "queries": []},
                "pageNo": page,
                "rows": 50,
                "sortBy": "createdAt",
                "sortOrder": "asc",
            })
            notes = data.get("Data", {}).get("data", [])
            if not notes:
                break

            texts, metas = [], []
            for n in notes:
                content = n.get("note", "") or n.get("description", "")
                if not content or len(content.strip()) < 20:
                    continue
                for chunk in chunk_text(content):
                    texts.append(chunk)
                    metas.append({
                        "source":      "salesmate",
                        "object_type": "note",
                        "object_id":   str(n.get("id", "")),
                        "object_name": n.get("title", "Note"),
                    })

            if texts:
                vector_store.upsert(texts, metas)
                total += len(texts)

            if len(notes) < 50:
                break
            page += 1
        print(f"  ✓ Notes done — {total} chunks")
    except Exception as e:
        print(f"  ✗ Notes failed: {e}")


def ingest_deals():
    """
    Ingest closed-won deals as case study material.
    Useful for RAG: 'We helped KRA with revenue management...'
    """
    print("[Ingest] Fetching Deals (closed-won)...")
    try:
        data = sm_post("/v4/deals/search", {
            "query": {
                "operator": "AND",
                "queries": [{"moduleName": "Deals", "field": "status", "operator": "eq", "value": "Won"}]
            },
            "pageNo": 1,
            "rows": 100,
            "sortBy": "closedDate",
            "sortOrder": "desc",
        })
        deals = data.get("Data", {}).get("data", [])

        texts, metas = [], []
        for d in deals:
            text = (
                f"Project/Deal: {d.get('name', '')}\n"
                f"Client: {d.get('company', {}).get('name', '') if isinstance(d.get('company'), dict) else ''}\n"
                f"Value: {d.get('dealValue', '')} {d.get('currency', '')}\n"
                f"Description: {d.get('description', '')}\n"
                f"Products: {d.get('products', '')}\n"
            )
            for chunk in chunk_text(text):
                texts.append(chunk)
                metas.append({
                    "source":      "salesmate",
                    "object_type": "deal",
                    "object_id":   str(d.get("id", "")),
                    "object_name": d.get("name", ""),
                })

        if texts:
            vector_store.upsert(texts, metas)
        print(f"  ✓ Deals done — {len(texts)} chunks")
    except Exception as e:
        print(f"  ✗ Deals failed: {e}")


def ingest_attachments():
    """
    Download PDF/DOCX attachments from Salesmate and extract text.
    These are brochures, proposals, case studies.
    """
    print("[Ingest] Fetching Attachments...")
    try:
        data = sm_post("/v4/attachments/search", {
            "query": {"operator": "AND", "queries": []},
            "pageNo": 1,
            "rows": 100,
        })
        attachments = data.get("Data", {}).get("data", [])
        total = 0

        for att in attachments:
            file_url  = att.get("fileUrl", "") or att.get("url", "")
            file_name = att.get("fileName", "") or att.get("name", "")
            ext = file_name.lower().split(".")[-1] if "." in file_name else ""

            if ext not in ("pdf", "docx", "doc", "txt"):
                continue

            try:
                resp = requests.get(file_url, headers=HEADERS, timeout=30)
                resp.raise_for_status()
                text = _extract_file_text(resp.content, ext, file_name)
                if not text or len(text.strip()) < 50:
                    continue

                chunks = chunk_text(text)
                metas  = [{
                    "source":      "salesmate",
                    "object_type": "attachment",
                    "object_id":   str(att.get("id", "")),
                    "object_name": file_name,
                } for _ in chunks]

                vector_store.upsert(chunks, metas)
                total += len(chunks)
                print(f"  Ingested: {file_name} ({len(chunks)} chunks)")
            except Exception as e:
                print(f"  ✗ Skipped {file_name}: {e}")

        print(f"  ✓ Attachments done — {total} chunks")
    except Exception as e:
        print(f"  ✗ Attachments failed: {e}")


def ingest_kb_articles():
    """Ingest Knowledge Base articles if Salesmate KB is enabled."""
    print("[Ingest] Fetching KB Articles...")
    try:
        # Salesmate KB may not be available on all plans
        data = sm_get("/v4/knowledge-articles", {"rows": 100, "pageNo": 1})
        articles = data.get("Data", {}).get("data", [])
        if not articles:
            print("  ℹ  No KB articles found (may not be enabled)")
            return

        texts, metas = [], []
        for a in articles:
            content = a.get("content", "") or a.get("body", "")
            title   = a.get("title", "Article")
            # Strip HTML from KB content
            from bs4 import BeautifulSoup
            text = BeautifulSoup(content, "lxml").get_text(" ", strip=True)
            text = f"Article: {title}\n\n{text}"

            for chunk in chunk_text(text):
                texts.append(chunk)
                metas.append({
                    "source":      "salesmate",
                    "object_type": "kb_article",
                    "object_id":   str(a.get("id", "")),
                    "object_name": title,
                })

        if texts:
            vector_store.upsert(texts, metas)
        print(f"  ✓ KB Articles done — {len(texts)} chunks")
    except Exception as e:
        print(f"  ✗ KB Articles failed (may not be available): {e}")


def _extract_file_text(content: bytes, ext: str, filename: str) -> str:
    """Extract raw text from PDF or DOCX bytes."""
    if ext == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )
    elif ext in ("docx", "doc"):
        from docx import Document
        doc = Document(io.BytesIO(content))
        return "\n".join(para.text for para in doc.paragraphs)
    elif ext == "txt":
        return content.decode("utf-8", errors="replace")
    return ""


# ── Main runner ───────────────────────────────────────────────────────────────

def run_full_ingest():
    print("\n=== Nexus Salesmate Ingest ===\n")
    vector_store.ensure_collection()

    ingest_products()
    ingest_notes()
    ingest_deals()
    ingest_attachments()
    ingest_kb_articles()

    count = vector_store.count()
    print(f"\n✓ Ingest complete — {count} total vectors in Qdrant\n")


if __name__ == "__main__":
    run_full_ingest()
