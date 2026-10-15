"""The data layer's tables in the application database (`app/database.py`).

- `datasets`: one row per stored dataset: its title, columns, quality report and provenance.
- `dataset_rows`: the cleaned values, one row per observation in the tidy form
  `period | dimensions | measure | value | unit` (the dimensions, such as industry or town, as JSON).
- `raw_downloads`: each source file exactly as downloaded, kept for auditing and re-cleaning.

This is the cache in the load order (fresh cache -> live source -> stale cache -> snapshot, see store.py). The
committed snapshots stay as files in the repository, so a fresh install works offline.
"""
import datetime as dt
import math
import threading

import pandas as pd
from sqlalchemy import JSON, Float, ForeignKey, Integer, LargeBinary, String, delete, insert, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.database import sqlite_engine

FIXED_COLUMNS = ("period", "measure", "value", "unit")


class Base(DeclarativeBase):
    pass


class DatasetRecord(Base):
    __tablename__ = "datasets"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)  # a curated key or a data.gov.sg dataset ID
    title: Mapped[str] = mapped_column(String(300))
    columns: Mapped[list] = mapped_column(JSON)      # the table's columns, in order
    rows: Mapped[int] = mapped_column(Integer)
    quality: Mapped[dict] = mapped_column(JSON)      # the cleaning report (data/cleaning.py)
    provenance: Mapped[dict] = mapped_column(JSON)   # source, agency, URLs, licence, retrieval time
    stored_at: Mapped[dt.datetime] = mapped_column()


class DatasetRow(Base):
    __tablename__ = "dataset_rows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_key: Mapped[str] = mapped_column(ForeignKey("datasets.key", ondelete="CASCADE"), index=True)
    period: Mapped[str] = mapped_column(String(20))
    dimensions: Mapped[dict] = mapped_column(JSON)
    measure: Mapped[str] = mapped_column(String(120))
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(60))


class RawDownload(Base):
    __tablename__ = "raw_downloads"
    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    format: Mapped[str] = mapped_column(String(8))  # csv, json or xlsx
    retrieved_at: Mapped[dt.datetime] = mapped_column()


_sessions: dict[str | None, sessionmaker] = {}
_sessions_lock = threading.Lock()


def dataset_sessions(url: str | None = None) -> sessionmaker:
    """Sessions on the dataset tables (created if missing). One engine per database, shared by every store.
    Runs start in parallel threads, so creating the tables is done once, under a lock: two threads creating
    them at the same moment would both try `CREATE TABLE`."""
    with _sessions_lock:
        if url not in _sessions:
            engine = sqlite_engine(url)
            Base.metadata.create_all(engine)
            _sessions[url] = sessionmaker(engine, expire_on_commit=False)
        return _sessions[url]


def raw_format(data: bytes) -> str:
    if data[:2] == b"PK":
        return "xlsx"
    return "json" if data.lstrip()[:1] in (b"{", b"[") else "csv"


def _text(value) -> str:
    return "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)


class DatasetTables:
    """Reads and writes datasets in the database: a whole table at a time, as the agents use them."""

    def __init__(self, url: str | None = None):
        self.sessions = dataset_sessions(url)

    def write(self, key: str, title: str, frame: pd.DataFrame, quality: dict, provenance: dict) -> None:
        """Store a cleaned table, replacing any earlier copy."""
        dimensions = [c for c in frame.columns if c not in FIXED_COLUMNS]
        rows = [{"dataset_key": key, "period": _text(r["period"]), "measure": _text(r["measure"]),
                 "unit": _text(r["unit"]), "value": None if pd.isna(r["value"]) else float(r["value"]),
                 "dimensions": {d: _text(r[d]) for d in dimensions}}
                for r in frame.to_dict("records")]
        with self.sessions() as db, db.begin():
            db.execute(delete(DatasetRow).where(DatasetRow.dataset_key == key))
            db.merge(DatasetRecord(key=key, title=title, columns=list(frame.columns), rows=len(rows),
                                   quality=quality, provenance=provenance, stored_at=dt.datetime.now(dt.UTC)))
            if rows:
                db.execute(insert(DatasetRow), rows)

    def read(self, key: str) -> tuple[pd.DataFrame, dict, dict] | None:
        """The stored table with its quality report and provenance, or None if it was never stored."""
        with self.sessions() as db:
            record = db.get(DatasetRecord, key)
            if record is None:
                return None
            rows = db.execute(select(DatasetRow.period, DatasetRow.dimensions, DatasetRow.measure, DatasetRow.value,
                                     DatasetRow.unit).where(DatasetRow.dataset_key == key)
                              .order_by(DatasetRow.id)).all()
        frame = pd.DataFrame([{"period": p, **dims, "measure": m, "value": v, "unit": u} for p, dims, m, v, u in rows],
                             columns=record.columns)
        frame["value"] = frame["value"].astype("float64")
        return frame, record.quality, record.provenance

    def save_raw(self, source: str, source_id: str, data: bytes) -> None:
        with self.sessions() as db, db.begin():
            db.merge(RawDownload(source=source, source_id=source_id, data=data, format=raw_format(data),
                                 retrieved_at=dt.datetime.now(dt.UTC)))

    def raw(self, source: str, source_id: str) -> RawDownload | None:
        with self.sessions() as db:
            return db.get(RawDownload, (source, source_id))
