"""Loads curated datasets in this order: fresh cache -> live source -> stale cache -> committed snapshot.

The cache is the dataset tables of the application database (data/database.py): each cleaned table, its quality
report and provenance, and the raw downloads. Snapshots are files committed to the repository. Every load records
where its data came from (provenance), so a source outage shows up in the report as a warning instead of being
hidden.
"""
import datetime as dt
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.data import catalogue
from app.data.cleaning import QualityReport
from app.data.database import DatasetTables
from app.data.http import SourceUnavailable
from app.data.registry import CURATED, CuratedDataset
from app.data.sources import DATA_DIR, SOURCES

SNAPSHOT_DIR = DATA_DIR / "snapshots"


class DataUnavailable(RuntimeError):
    """No live source, cache or snapshot could provide the dataset."""


class Provenance(BaseModel):
    origin: Literal["live", "cache", "snapshot"]
    source: str
    agency: str
    urls: list[str]
    licence: str
    retrieved_at: str  # when the data was fetched from its source (UTC, ISO 8601)
    warning: str | None = None


@dataclass
class LoadedDataset:
    dataset: CuratedDataset
    frame: pd.DataFrame
    quality: QualityReport
    provenance: Provenance


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class DataStore:
    def __init__(self, settings: Settings | None = None, database: str | None = None,
                 snapshot_dir: Path = SNAPSHOT_DIR, sources: dict | None = None):
        self.settings = settings or get_settings()
        self.tables = DatasetTables(database)  # default: the application database
        self.snapshot_dir = snapshot_dir
        self.sources = sources or {name: cls() for name, cls in SOURCES.items()}

    def describe(self, key: str) -> CuratedDataset:
        """A curated dataset by key, or any CSV dataset in the data.gov.sg catalogue by its ID."""
        if key in CURATED:
            return CURATED[key]
        row = catalogue.find(key)
        if row is None:
            raise DataUnavailable(f"unknown dataset '{key}'")
        if row["format"] != "CSV":
            raise DataUnavailable(f"{row['name']}: only CSV datasets can be loaded without curation")

        def clean(raw):
            return catalogue.infer_tidy(pd.read_csv(io.BytesIO(raw[key]), dtype=str, keep_default_na=False), key)

        return CuratedDataset(key, row["name"], row["managedByAgencyName"], "uncurated", row.get("description") or "",
                              "datagovsg", (key,), "none", clean,
                              "Uncurated: structure inferred automatically. Check units, totals and definitions "
                              "before drawing conclusions.")

    def load(self, key: str) -> LoadedDataset:
        dataset = self.describe(key)
        cached = self._read_cache(dataset)
        fresh = cached and self._age_hours(cached) < self.settings.data_max_age_hours
        if fresh and not self.settings.force_source_fail:
            return cached
        try:
            if self.settings.force_source_fail:
                raise SourceUnavailable("FORCE_SOURCE_FAIL is on")
            raws = {source_id: self._fetch(dataset, source_id) for source_id in dataset.source_ids}
        except SourceUnavailable as error:
            fallback = cached or self._read(self.snapshot_dir, dataset, "snapshot")
            if fallback is None:
                message = f"{dataset.title}: live source unavailable and no stored copy ({error})"
                raise DataUnavailable(message) from error
            fallback.provenance.warning = (f"Live source unavailable ({error}); using {fallback.provenance.origin} "
                                           f"data retrieved {fallback.provenance.retrieved_at[:10]}")
            return fallback
        frame, quality = dataset.clean(raws)
        loaded = LoadedDataset(dataset, frame, quality, self.provenance(dataset, "live", now().isoformat()))
        self.tables.write(dataset.key, dataset.title, frame, quality.model_dump(), loaded.provenance.model_dump())
        return loaded

    def provenance(self, dataset: CuratedDataset, origin: str, retrieved_at: str) -> Provenance:
        source = self.sources[dataset.source]
        return Provenance(origin=origin, source=source.name, agency=dataset.agency, licence=source.licence,
                          urls=[source.page_url(sid) for sid in dataset.source_ids], retrieved_at=retrieved_at)

    def _fetch(self, dataset: CuratedDataset, source_id: str) -> bytes:
        data = self.sources[dataset.source].download(source_id)
        self.tables.save_raw(dataset.source, source_id, data)  # kept for auditing and re-cleaning
        return data

    def _read_cache(self, dataset: CuratedDataset) -> LoadedDataset | None:
        stored = self.tables.read(dataset.key)
        if stored is None:
            return None
        frame, quality, provenance = stored
        return LoadedDataset(dataset, frame, QualityReport(**quality),
                             Provenance(**{**provenance, "origin": "cache", "warning": None}))

    @staticmethod
    def write_snapshot(directory: Path, loaded: LoadedDataset) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        key = loaded.dataset.key
        # mtime=0 keeps the gzip header fixed, so an unchanged table gives an identical file (no noisy diffs)
        loaded.frame.to_csv(directory / f"{key}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        meta = {"quality": loaded.quality.model_dump(), "provenance": loaded.provenance.model_dump()}
        (directory / f"{key}.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))

    @staticmethod
    def _read(directory: Path, dataset: CuratedDataset, origin: str) -> LoadedDataset | None:
        """A committed snapshot (csv.gz with its quality report and provenance in JSON)."""
        data_path, meta_path = directory / f"{dataset.key}.csv.gz", directory / f"{dataset.key}.json"
        if not (data_path.is_file() and meta_path.is_file()):
            return None
        frame = pd.read_csv(data_path, dtype=str, keep_default_na=False)
        frame["value"] = frame["value"].astype("float64")
        meta = json.loads(meta_path.read_text())
        provenance = Provenance(**{**meta["provenance"], "origin": origin, "warning": None})
        return LoadedDataset(dataset, frame, QualityReport(**meta["quality"]), provenance)

    @staticmethod
    def _age_hours(loaded: LoadedDataset) -> float:
        return (now() - dt.datetime.fromisoformat(loaded.provenance.retrieved_at)).total_seconds() / 3600
