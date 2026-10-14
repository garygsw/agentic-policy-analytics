"""Crawls agency newsrooms into the document index, politely and incrementally.

    uv run python -m app.rag.ingest                                   # every source, recent pages
    uv run python -m app.rag.ingest --sources mom,moh --since 2025-01-01 --limit 50
    uv run python -m app.rag.ingest --catalogue                       # the data.gov.sg dataset catalogue

- robots.txt is honoured: disallowed pages are skipped and its Crawl-delay is used when it sets one.
- Incremental: a page is fetched only if it is new or its sitemap lastmod changed, and text that did not change
  is not embedded again.
- Raw HTML is not kept. Agency terms do not allow republishing their content, so the index stays on this
  machine (var/rag) and is never committed.
"""
import argparse
import datetime as dt
import hashlib
import re
import time
from collections.abc import Callable
from urllib.robotparser import RobotFileParser

import trafilatura

from app.data import catalogue
from app.data.http import SourceUnavailable, fetch
from app.data.sources import DataGovSg
from app.rag.chunking import Chunk, chunk_document
from app.rag.embed import Embedder, get_embedder
from app.rag.index import DocumentIndex
from app.rag.search import open_index
from app.rag.sources import DOCUMENT_SOURCES, DocumentSource, doc_type

MIN_CHARS = 200          # pages with less text are navigation or error pages
DEFAULT_SINCE = "2023-01-01"
DEFAULT_LIMIT = 500      # newest pages per source on each run
ROBOTS_AGENT = "policy-analytics"


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def sitemap_entries(xml: str) -> list[tuple[str, str]]:
    """(url, lastmod) for every <url> in a sitemap."""
    entries = []
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", block)
        lastmod = re.search(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", block)
        if loc:
            entries.append((loc.group(1), lastmod.group(1)[:10] if lastmod else ""))
    return entries


def select(entries: list[tuple[str, str]], include: str, since: str, limit: int) -> list[tuple[str, str]]:
    """Pages matching the source's pattern, changed since `since`, newest first, at most `limit`."""
    pattern = re.compile(include)
    chosen = [(url, lastmod) for url, lastmod in entries if pattern.search(url) and lastmod >= since]
    return sorted(chosen, key=lambda e: e[1], reverse=True)[:limit]


def extract(html: str, url: str) -> dict | None:
    """Main text, title and publication date of a page; None if it has too little text to be an article."""
    result = trafilatura.bare_extraction(html, url=url, with_metadata=True, include_tables=True,
                                         favor_precision=True)
    data = result.as_dict() if hasattr(result, "as_dict") else (result or {})
    text = (data.get("text") or "").strip()
    if len(text) < MIN_CHARS:
        return None
    title = (data.get("title") or "").strip()
    first_line = text.split("\n", 1)[0].strip()
    if not title or re.match(r"^\d{4}\b", title):  # MOM titles come from URL slugs like "2409 Recommendations"
        title = first_line[:200]
    return {"title": title, "published": data.get("date") or "", "text": text}


def robots_for(source: DocumentSource, get: Callable[[str], str]) -> RobotFileParser:
    base = re.match(r"https?://[^/]+", source.sitemap).group(0)
    parser = RobotFileParser()
    try:
        parser.parse(get(f"{base}/robots.txt").splitlines())
    except SourceUnavailable:
        parser.parse([])  # no robots.txt: everything allowed, default delay
    return parser


def http_get(url: str) -> str:
    return fetch(url, attempts=2, backoff_s=5).text


def ingest_source(source: DocumentSource, index: DocumentIndex, embedder: Embedder, *, since: str = DEFAULT_SINCE,
                  limit: int = DEFAULT_LIMIT, get: Callable[[str], str] = http_get,
                  sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> dict:
    run = {"source": source.key, "started": now_iso(), "seen": 0, "new": 0, "updated": 0, "unchanged": 0,
           "skipped": 0, "failed": 0, "notes": ""}
    robots = robots_for(source, get)
    delay = robots.crawl_delay(ROBOTS_AGENT) or source.crawl_delay_s
    pages = select(sitemap_entries(get(source.sitemap)), source.include, since, limit)
    run["seen"] = len(pages)
    for url, lastmod in pages:
        existing = index.document(url)
        if existing and existing["lastmod"] >= lastmod:
            run["unchanged"] += 1
            continue
        if not robots.can_fetch(ROBOTS_AGENT, url):
            run["skipped"] += 1
            continue
        sleep(delay)
        try:
            page = extract(get(url), url)
        except Exception as error:  # noqa: BLE001 - one bad page must not stop the run
            run["failed"] += 1
            log(f"  failed {url}: {type(error).__name__}: {str(error)[:120]}")
            continue
        if page is None:
            run["skipped"] += 1
            continue
        content_hash = hashlib.sha256(page["text"].encode()).hexdigest()
        if existing and existing["content_hash"] == content_hash:
            index.touch(url, lastmod)  # republished without changes: nothing to embed again
            run["unchanged"] += 1
            continue
        kind = doc_type(url, page["title"])
        chunks = chunk_document(page["title"], page["text"], kind)
        vectors = embedder.embed([f"{c.header}\n{c.text}" for c in chunks])
        index.upsert({"url": url, "source": source.key, "agency": source.agency, "doc_type": kind,
                      "title": page["title"], "published": page["published"], "lastmod": lastmod,
                      "content_hash": content_hash, "fetched_at": now_iso()}, chunks, vectors)
        run["updated" if existing else "new"] += 1
    run["finished"] = now_iso()
    index.record_run(run)
    return run


def index_catalogue(index: DocumentIndex, embedder: Embedder, batch: int = 256) -> dict:
    """Index every data.gov.sg dataset's name and description, so dataset discovery can use hybrid search."""
    run = {"source": "datagovsg-catalogue", "started": now_iso(), "seen": 0, "new": 0, "updated": 0,
           "unchanged": 0, "skipped": 0, "failed": 0, "notes": ""}
    rows = catalogue.load_catalogue()
    run["seen"] = len(rows)
    pending = []
    for row in rows:
        text = f"{row['name']}. {row.get('description') or ''} Agency: {row['managedByAgencyName']}."
        url = DataGovSg.page_url(row["datasetId"])
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        existing = index.document(url)
        if existing and existing["content_hash"] == content_hash:
            run["unchanged"] += 1
            continue
        doc = {"url": url, "source": "datagovsg", "agency": row["managedByAgencyName"], "doc_type": "dataset",
               "title": row["name"], "published": row["lastUpdatedAt"][:10], "lastmod": row["lastUpdatedAt"][:10],
               "content_hash": content_hash, "fetched_at": now_iso()}
        pending.append((doc, [Chunk(0, row["name"], text, 0)], bool(existing)))
    for start in range(0, len(pending), batch):
        part = pending[start:start + batch]
        vectors = embedder.embed([chunks[0].text for _, chunks, _ in part])
        for (doc, chunks, existed), vector in zip(part, vectors, strict=True):
            index.upsert(doc, chunks, [vector])
            run["updated" if existed else "new"] += 1
    run["finished"] = now_iso()
    index.record_run(run)
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl agency newsrooms into the document index")
    parser.add_argument("--sources", default=",".join(DOCUMENT_SOURCES), help="comma-separated source keys")
    parser.add_argument("--since", default=DEFAULT_SINCE, help="only pages changed since this date")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="newest pages per source")
    parser.add_argument("--catalogue", action="store_true", help="index the data.gov.sg dataset catalogue only")
    args = parser.parse_args()
    index, embedder = open_index(), get_embedder()
    if args.catalogue:
        print(index_catalogue(index, embedder))
        return
    for key in [k for k in args.sources.split(",") if k]:
        started = time.time()
        run = ingest_source(DOCUMENT_SOURCES[key], index, embedder, since=args.since, limit=args.limit)
        print(f"{key}: {run['seen']} pages, {run['new']} new, {run['updated']} updated, {run['unchanged']} unchanged, "
              f"{run['skipped']} skipped, {run['failed']} failed ({time.time() - started:.0f}s)")
    print(index.stats())


if __name__ == "__main__":
    main()
