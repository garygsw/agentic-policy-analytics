"""Write the committed fire-hotspot snapshot from NASA FIRMS. Needs FIRMS_MAP_KEY; takes about 20 minutes.

    uv run python scripts/make_hotspots.py           # download, then clean and write the snapshot
    uv run python scripts/make_hotspots.py --reuse   # clean the download kept in the database again

Downloads MODIS fire detections over Southeast Asia for July to October of every year from 2014, five days per
request (the API's limit): the archive ("standard processing") up to its last date, near-real-time data after it.
The combined download is kept in the database for auditing; the registry's cleaner turns it into the snapshot.
"""
import argparse
import datetime as dt
import io
import time

import pandas as pd

from app.config import get_settings
from app.data.registry import CURATED, HOTSPOTS
from app.data.sources import Firms
from app.data.store import SNAPSHOT_DIR, DataStore, LoadedDataset

BOX = "92,-12,142,24"  # Southeast Asia: west, south, east, north
FIRST_YEAR = 2014
SEASON = ((7, 1), (10, 31))  # July to October
DAYS_PER_REQUEST = 5


def windows(first: dt.date, last: dt.date, archive_end: dt.date):
    """(product, start, days) requests covering first..last: the archive up to archive_end, near-real-time after."""
    start = first
    while start <= last:
        product, limit = ("MODIS_SP", archive_end) if start <= archive_end else ("MODIS_NRT", last)
        end = min(start + dt.timedelta(days=DAYS_PER_REQUEST - 1), last, limit)
        yield product, start, (end - start).days + 1
        start = end + dt.timedelta(days=1)


def download(firms: Firms, today: dt.date) -> bytes:
    availability = pd.read_csv(io.StringIO(firms.get("data_availability/csv/{key}/ALL")))
    archive_end = dt.date.fromisoformat(availability.set_index("data_id").loc["MODIS_SP", "max_date"])
    parts = []
    for year in range(FIRST_YEAR, today.year + 1):
        first, last = dt.date(year, *SEASON[0]), min(dt.date(year, *SEASON[1]), today)
        for product, start, days in windows(first, last, archive_end):
            part = pd.read_csv(io.StringIO(firms.area(product, BOX, start.isoformat(), days)))
            parts.append(part)
            print(f"{product:9s} {start} +{days}d: {len(part):6,} detections", flush=True)
            time.sleep(1)  # be gentle with a free API
    combined = pd.concat(parts, ignore_index=True)
    return combined.to_csv(index=False).encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reuse", action="store_true", help="clean the download kept in the database again")
    args = parser.parse_args()
    dataset, store = CURATED["fire_hotspots"], DataStore()
    stored = store.tables.raw("firms", HOTSPOTS)
    if args.reuse and stored:
        raw, retrieved = stored.data, stored.retrieved_at.replace(tzinfo=dt.UTC)
    else:
        key = get_settings().firms_map_key
        raw = download(Firms(key.get_secret_value() if key else ""), dt.date.today())
        store.tables.save_raw("firms", HOTSPOTS, raw)
        retrieved = dt.datetime.now(dt.UTC)
    frame, quality = dataset.clean({HOTSPOTS: raw})
    store.write_snapshot(SNAPSHOT_DIR, LoadedDataset(dataset, frame, quality,
                                                     store.provenance(dataset, "snapshot", retrieved.isoformat())))
    print(f"fire_hotspots: {len(frame):,} rows; {quality.notes[-1]}")


if __name__ == "__main__":
    main()
