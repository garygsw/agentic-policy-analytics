"""Write the committed snapshots: cleaned copies of every curated dataset, used when a live source is down.

    uv run python scripts/make_snapshots.py          # reuse raw files already in var/cache/raw
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
    parser.add_argument("--fetch", action="store_true", help="download fresh raw files instead of reusing cached ones")
    args = parser.parse_args()
    store = DataStore()
    for key, dataset in CURATED.items():
        raws, mtimes = {}, []
        for source_id in dataset.source_ids:
            cached = sorted(store.raw_dir.glob(f"{dataset.source}_{source_id}.*"))
            if cached and not args.fetch:
                raws[source_id] = cached[0].read_bytes()
                mtimes.append(cached[0].stat().st_mtime)
            else:
                raws[source_id] = store._fetch(dataset, source_id)
                mtimes.append(time.time())
                if dataset.source == "datagovsg":
                    time.sleep(5)  # stay under data.gov.sg's rate limit
        frame, quality = dataset.clean(raws)
        retrieved = dt.datetime.fromtimestamp(min(mtimes), dt.UTC).isoformat()
        store.write(SNAPSHOT_DIR, LoadedDataset(dataset, frame, quality,
                                                store.provenance(dataset, "snapshot", retrieved)))
        size = (SNAPSHOT_DIR / f"{key}.csv.gz").stat().st_size
        print(f"{key:34s} {len(frame):7,} rows  {size / 1e3:7.1f} kB")


if __name__ == "__main__":
    main()
