"""Write the committed snapshots: cleaned copies of every curated dataset, used when a live source is down.

    uv run python scripts/make_snapshots.py          # reuse raw downloads already in the database
    uv run python scripts/make_snapshots.py --fetch  # download everything again first (slow: rate limits)

Snapshots of data.gov.sg datasets are redistributed under the Singapore Open Data Licence. The GDP table
comes from SingStat's API; the same table is published on data.gov.sg under that licence.
"""
import argparse
import datetime as dt
import time

from app.data.registry import CURATED
from app.data.store import SNAPSHOT_DIR, DataStore, LoadedDataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fetch", action="store_true", help="download fresh raw files instead of reusing stored ones")
    parser.add_argument("--only", help="comma-separated dataset keys (default: every curated dataset)")
    args = parser.parse_args()
    store = DataStore()
    keys = args.only.split(",") if args.only else list(CURATED)
    for key, dataset in ((k, CURATED[k]) for k in keys):
        raws, mtimes = {}, []
        for source_id in dataset.source_ids:
            stored = store.tables.raw(dataset.source, source_id)
            if stored and not args.fetch:
                raws[source_id] = stored.data
                mtimes.append(stored.retrieved_at.replace(tzinfo=dt.UTC).timestamp())
            else:
                raws[source_id] = store._fetch(dataset, source_id)
                mtimes.append(time.time())
                if dataset.source == "datagovsg":
                    time.sleep(5)  # stay under data.gov.sg's rate limit
        frame, quality = dataset.clean(raws)
        retrieved = dt.datetime.fromtimestamp(min(mtimes), dt.UTC).isoformat()
        store.write_snapshot(SNAPSHOT_DIR, LoadedDataset(dataset, frame, quality,
                                                         store.provenance(dataset, "snapshot", retrieved)))
        size = (SNAPSHOT_DIR / f"{key}.csv.gz").stat().st_size
        print(f"{key:34s} {len(frame):7,} rows  {size / 1e3:7.1f} kB")


if __name__ == "__main__":
    main()
