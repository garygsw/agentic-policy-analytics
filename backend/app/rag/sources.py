"""Agency newsrooms the ingest service crawls, and what kind of document each page is.

Each source is one sitemap plus a URL pattern for the pages worth indexing. Adding an agency is one entry here.
Every site's robots.txt is honoured (see ingest.py); the delay below applies when robots.txt sets none.
MAS is not included: its sitemap is an HTML page rather than XML.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentSource:
    key: str
    agency: str
    sitemap: str
    include: str            # regex a page URL must match
    crawl_delay_s: float = 1.0


DOCUMENT_SOURCES = {s.key: s for s in [
    DocumentSource("mom", "Ministry of Manpower", "https://www.mom.gov.sg/newsroom.xml",
                   r"/newsroom/(press-releases|speeches|parliament-questions-and-replies|press-replies|announcements|"
                   r"mom-statements)/"),
    DocumentSource("mof", "Ministry of Finance", "https://www.mof.gov.sg/sitemap.xml", r"/news-resources/newsroom/."),
    DocumentSource("pmo", "Prime Minister's Office", "https://www.pmo.gov.sg/sitemap.xml", r"/newsroom/."),
    DocumentSource("moh", "Ministry of Health", "https://www.moh.gov.sg/sitemap.xml", r"/newsroom/."),
    DocumentSource("ura", "Urban Redevelopment Authority", "https://www.ura.gov.sg/sitemap.xml",
                   r"/(news/media|news/articles|land-planning/master-plan|land-planning/long-term-plan)/."),
]}

DOC_TYPES = [  # first match wins; checked against the URL and the title
    ("parliament", r"parliament|parliamentary|\bpq\b|written-answer|oral-answer"),
    ("speech", r"speech|remarks|address|keynote|transcript|interview|dialogue"),
    ("statement", r"statement|press-repl|fact-check|clarification|response"),
    ("planning", r"master-plan|long-term-plan|land-planning"),
    ("press_release", r"press-release|media-release|news/media|announcement"),
]


def doc_type(url: str, title: str) -> str:
    text = f"{url} {title}".lower()
    return next((kind for kind, pattern in DOC_TYPES if re.search(pattern, text)), "news")
