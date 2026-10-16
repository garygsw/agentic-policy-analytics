"""What one analysis has loaded and computed, numbered for citation: sources S1.., facts F1.., charts C1..

The workspace is rebuilt from graph state in each agent step (sources travel in state; the tables themselves are
reloaded from the local cache), so nothing large is ever stored in a checkpoint.
"""
import json
import math

from app.analysis.stats import RULE_WORDS, dimensions
from app.data.registry import CURATED
from app.data.store import SNAPSHOT_DIR, DataStore, LoadedDataset

PREFIX = {"dataset": "S", "document": "D", "web": "W"}
MAX_PASSAGE_CHARS = 1500


def round_value(value: float, unit: str) -> float | int:
    """The precision a report should quote, decided once here so the report and the check agree."""
    if unit == "%":
        return round(value, 1)
    if unit == "Pearson r":
        return round(value, 2)
    return int(value) if float(value).is_integer() else round(value, 2)


class Workspace:
    def __init__(self, store: DataStore, sources: list[dict] | None = None, facts: list[dict] | None = None,
                 charts: list[dict] | None = None):
        """Earlier sources, facts and charts (from previous steps of the same turn) keep their IDs; new ones
        continue the numbering."""
        self.store = store
        self.sources: list[dict] = [dict(s) for s in sources or []]
        self.facts: list[dict] = [dict(f) for f in facts or []]
        self.charts: list[dict] = [dict(c) for c in charts or []]
        self._loaded: dict[str, LoadedDataset] = {}

    def dataset(self, dataset_id: str) -> tuple[LoadedDataset, str]:
        """Load (once per workspace) and register the dataset as a citable source."""
        if dataset_id not in self._loaded:
            self._loaded[dataset_id] = self.store.load(dataset_id)
        loaded = self._loaded[dataset_id]
        return loaded, self._source_id(loaded)

    def _source_id(self, loaded: LoadedDataset) -> str:
        for source in self.sources:
            if source.get("dataset_id") == loaded.dataset.key:
                return source["id"]
        p = loaded.provenance
        source = {"id": self._next_id("dataset"), "kind": "dataset", "dataset_id": loaded.dataset.key,
                  "title": loaded.dataset.title,
                  "agency": loaded.dataset.agency, "publisher": p.source, "url": p.urls[0], "licence": p.licence,
                  "origin": p.origin, "retrieved_at": p.retrieved_at[:10], "warning": p.warning,
                  "quality": loaded.quality.summary(), "notes": loaded.dataset.notes}
        self.sources.append(source)
        return source["id"]

    def _next_id(self, kind: str) -> str:
        count = sum(1 for s in self.sources if s.get("kind", "dataset") == kind)
        return f"{PREFIX[kind]}{count + 1}"

    def add_passages(self, kind: str, passages: list[dict]) -> list[dict]:
        """Register document passages (D#) or web results (W#) as citable sources; repeats keep their ID."""
        registered = []
        for p in passages:
            key = p.get("key") or f"{p['url']}#{p.get('chunk_id', '')}"  # web results bring a normalised key
            existing = next((s for s in self.sources if s.get("key") == key), None)
            if existing is None:
                existing = {"id": self._next_id(kind), "kind": kind, "key": key, "title": p["title"],
                            "agency": p.get("agency", ""), "url": p["url"], "published": p.get("published", ""),
                            "doc_type": p.get("doc_type", kind), "text": p["text"][:MAX_PASSAGE_CHARS],
                            "untrusted": kind == "web"}
                self.sources.append(existing)
            registered.append(existing)
        return registered

    def add_fact(self, label: str, value: float, unit: str, sources: list[str], method: str = "") -> str:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            raise ValueError(f"no value for {label}")
        fact = {"id": f"F{len(self.facts) + 1}", "label": label, "value": round_value(value, unit), "unit": unit,
                "sources": sources, "method": method}
        self.facts.append(fact)
        return fact["id"]

    def add_chart(self, title: str, spec: dict, table: dict, sources: list[str], kind: str = "", note: str = "") -> str:
        """The kind and the note (e.g. places left off a map) are passed on to the report writer."""
        chart = {"id": f"C{len(self.charts) + 1}", "title": title, "spec": spec, "table": table, "sources": sources,
                 "kind": kind, "note": note}
        self.charts.append(chart)
        return chart["id"]

    def passages(self) -> list[dict]:
        return [s for s in self.sources if s.get("kind") in ("document", "web")]

    def profiles(self) -> list[dict]:
        return [profile(loaded, self._source_id(loaded)) for loaded in self._loaded.values()]


def profile(loaded: LoadedDataset, source_id: str) -> dict:
    """What an agent needs to know to query a dataset correctly."""
    frame, dataset = loaded.frame, loaded.dataset
    measures = {measure: {"unit": unit, "can_add_up_across": list(dataset.summable.get(measure, ()))}
                for measure, unit in frame[["measure", "unit"]].drop_duplicates().itertuples(index=False)}
    if dataset.frequency in ("monthly", "quarterly"):  # how its periods make a year, or a season of chosen months
        for measure, about in measures.items():
            about["periods_combine_as"] = RULE_WORDS[dataset.rule_over_time(measure)]
    dims = {dim: {"distinct_values": int(frame[dim].nunique()), "examples": sorted(frame[dim].unique())[:12]}
            for dim in dimensions(frame)}
    return {"dataset_id": dataset.key, "source_id": source_id, "title": dataset.title, "agency": dataset.agency,
            "frequency": dataset.frequency, "coverage": loaded.quality.coverage, "measures": measures,
            "dimensions": dims, "data_quality": loaded.quality.summary(), "origin": loaded.provenance.origin,
            "warning": loaded.provenance.warning, "caveats": dataset.notes}


def curated_titles() -> str:
    """One short line per curated dataset, for the coordinator: enough to know what data exists."""
    lines = []
    for key, dataset in CURATED.items():
        meta_path = SNAPSHOT_DIR / f"{key}.json"
        coverage = json.loads(meta_path.read_text())["quality"]["coverage"] if meta_path.is_file() else None
        span = f", {coverage[0]} to {coverage[1]}" if coverage else ""
        lines.append(f"- {dataset.title} ({dataset.agency}{span})")
    return "\n".join(lines)


def curated_catalogue() -> str:
    """One line per curated dataset, for the Extraction agent's prompt (coverage read from the snapshots)."""
    lines = []
    for key, dataset in CURATED.items():
        meta_path = SNAPSHOT_DIR / f"{key}.json"
        coverage = json.loads(meta_path.read_text())["quality"]["coverage"] if meta_path.is_file() else None
        span = f"{coverage[0]} to {coverage[1]}" if coverage else "no time dimension"
        lines.append(f"- {key}: {dataset.title} ({dataset.agency}; {dataset.frequency}; {span}). "
                     f"{dataset.description}." + (f" Caveat: {dataset.notes}" if dataset.notes else ""))
    return "\n".join(lines)
