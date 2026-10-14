"""Discovery across the whole data.gov.sg catalogue, beyond the curated datasets.

A copy of the catalogue metadata is committed (data/catalogue/datagovsg.jsonl.gz) so search works offline. The
ingest service refreshes it; to refresh by hand: uv run python -m app.data.catalogue refresh
"""
import gzip
import json
import math
import re
import sys
import time
from collections import Counter
from functools import lru_cache

import pandas as pd

from app.data.cleaning import MISSING_MARKERS, Frequency, QualityReport, tidy
from app.data.sources import DATA_DIR, DataGovSg

CATALOGUE_FILE = DATA_DIR / "catalogue" / "datagovsg.jsonl.gz"
WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = {"the", "of", "and", "in", "by", "for", "to", "a", "an", "on", "at", "from", "with", "per"}


def refresh(source: DataGovSg | None = None, delay_s: float = 0.25) -> int:
    source = source or DataGovSg()
    rows, page, pages = [], 1, 1
    while page <= pages:
        data = source.catalogue_page(page)
        pages = data["pages"]
        rows += data["datasets"]
        page += 1
        time.sleep(delay_s)
    CATALOGUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(CATALOGUE_FILE, "wt", encoding="utf-8") as f:
        f.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    load_catalogue.cache_clear()
    return len(rows)


@lru_cache(maxsize=1)
def load_catalogue() -> list[dict]:
    if not CATALOGUE_FILE.is_file():
        return []
    with gzip.open(CATALOGUE_FILE, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def _words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOPWORDS]


def search(query: str, limit: int = 10) -> list[dict]:
    """Keyword search, words weighted by rarity (IDF), title matches counting double.
    The hybrid retrieval index will add semantic search over the same metadata."""
    rows = load_catalogue()
    terms = set(_words(query))
    if not rows or not terms:
        return []
    documents = [(row, set(_words(row["name"])), set(_words(row.get("description") or ""))) for row in rows]
    frequency = Counter(word for _, title, body in documents for word in title | body)
    n = len(rows)  # BM25's smoothed IDF: rarer words weigh more, and every match stays positive
    idf = {term: math.log(1 + (n - frequency[term] + 0.5) / (frequency[term] + 0.5)) for term in terms}
    scored = []
    for row, title, body in documents:
        score = sum(idf[t] * (2 if t in title else 1) for t in terms if t in title or t in body)
        if score > 0:
            scored.append((score, row["lastUpdatedAt"], row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [{"dataset_id": row["datasetId"], "title": row["name"], "agency": row["managedByAgencyName"],
             "format": row["format"], "last_updated": row["lastUpdatedAt"][:10]} for _, _, row in scored[:limit]]


def find(dataset_id: str) -> dict | None:
    return next((row for row in load_catalogue() if row["datasetId"] == dataset_id), None)


# -- generic cleaning for uncurated datasets ------------------------------------------------------------------
PERIOD_COLUMNS: dict[str, Frequency] = {"year": "annual", "quarter": "quarterly", "month": "monthly",
                                        "financial_year": "financial_year"}
MAX_DIMENSION_VALUES = 200


def _numeric_share(values: pd.Series) -> float:
    text = values.astype("string").str.strip()
    present = text[~(text.isna() | text.str.lower().isin(MISSING_MARKERS))]
    if present.empty:
        return 0.0
    return float(pd.to_numeric(present.str.replace(r"[,$%]", "", regex=True), errors="coerce").notna().mean())


def infer_tidy(df: pd.DataFrame, dataset_id: str) -> tuple[pd.DataFrame, QualityReport]:
    """Best-effort reshaping of a dataset nobody has curated. Units and totals are not known, so the
    quality report says so."""
    notes = ["uncurated: structure inferred automatically; units and total rows are not verified"]
    year_columns = [c for c in df.columns if re.fullmatch(r"\d{4}", str(c).strip())]
    if len(year_columns) >= 3:  # wide statistical table: one series per row, one column per year
        label = next(c for c in df.columns if c not in year_columns)
        df = df.melt(id_vars=[label], value_vars=year_columns, var_name="year", value_name="value")
        df = df.rename(columns={label: "series"})
        frame, report = tidy(df, dataset_id, period="year", frequency="annual", dims=["series"],
                             measures={"value": "as published"})
        report.notes += notes
        return frame, report
    period = next((c for c in df.columns if c.lower() in PERIOD_COLUMNS), None)
    measures = {c: "as published" for c in df.columns if c != period and _numeric_share(df[c]) >= 0.8}
    candidates = [c for c in df.columns if c != period and c not in measures]
    dims = [c for c in candidates if df[c].nunique() <= MAX_DIMENSION_VALUES]
    dropped = [c for c in candidates if c not in dims]
    if dropped:  # identifiers, free text and names: not useful for aggregates, and may be personal
        notes.append("dropped high-cardinality text columns: " + ", ".join(dropped))
    if not measures:
        raise ValueError(f"{dataset_id}: no numeric columns found")
    frame, report = tidy(df, dataset_id, period=period, frequency=PERIOD_COLUMNS.get((period or "").lower(), "none"),
                         dims=dims, measures=measures)
    report.notes += notes
    return frame, report


if __name__ == "__main__" and sys.argv[1:] == ["refresh"]:
    print(f"catalogue refreshed: {refresh()} datasets")
