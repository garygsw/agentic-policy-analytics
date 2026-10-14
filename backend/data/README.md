# Data in this folder

| Folder | What it is |
|---|---|
| `snapshots/` | Cleaned copies of the curated datasets (`<key>.csv.gz`), each with its data-quality report and provenance (`<key>.json`). Used when a live source is unavailable. Rebuild with `uv run python scripts/make_snapshots.py`. |
| `catalogue/` | Metadata for every dataset on data.gov.sg (names, descriptions, agencies), used to discover datasets beyond the curated set. Refresh with `uv run python -m app.data.catalogue refresh`. |
| `internal/` | `tech_talent_programmes.xlsx`: **synthetic** records of a fictional agency, standing in for an internal system. Not an official statistic. Rebuild with `uv run python scripts/make_internal_mock.py`. |

## Attribution and licence

Contains information from data.gov.sg, which is made available under the terms of the
[Singapore Open Data Licence version 1.0](https://data.gov.sg/open-data-licence). Each snapshot's `.json`
file names the publishing agency and links to the source dataset.

The GDP-by-industry snapshot is fetched from the Department of Statistics' SingStat Table Builder API. The same
table is published on data.gov.sg (dataset `d_d0db56a7c201c9a1e42c846d4fe6da8f`) under the licence above.

Snapshots are processed data: personal data columns are removed, and some datasets are aggregated (for
example, monthly medians from individual transactions). See each dataset's quality report for what changed.
