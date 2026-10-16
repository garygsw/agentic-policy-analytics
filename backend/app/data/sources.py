"""Clients for the three data sources. Each returns raw bytes; cleaning happens elsewhere.

- data.gov.sg: the national open-data portal (CSV and GeoJSON files through a REST API, JSON metadata)
- SingStat Table Builder: the Department of Statistics' JSON API
- Internal: a mock internal agency system, an Excel workbook of synthetic data
"""
import time
from pathlib import Path

from app.data.http import SourceUnavailable, fetch

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class DataGovSg:
    name = "data.gov.sg"
    licence = "Singapore Open Data Licence v1.0 (https://data.gov.sg/open-data-licence)"
    DOWNLOAD_API = "https://api-open.data.gov.sg/v1/public/api/datasets"
    METADATA_API = "https://api-production.data.gov.sg/v2/public/api/datasets"

    def __init__(self, **fetch_options):
        self.fetch_options = fetch_options

    def download(self, dataset_id: str, poll_attempts: int = 10) -> bytes:
        """Ask for a download link (large files are prepared asynchronously), then fetch the file."""
        for _ in range(poll_attempts):
            body = fetch(f"{self.DOWNLOAD_API}/{dataset_id}/poll-download", **self.fetch_options).json()
            data = body.get("data") or {}
            if data.get("url"):
                return fetch(data["url"], **self.fetch_options).content
            time.sleep(3)
        raise SourceUnavailable(f"data.gov.sg did not prepare a download for {dataset_id}")

    def metadata(self, dataset_id: str) -> dict:
        return fetch(f"{self.METADATA_API}/{dataset_id}/metadata", **self.fetch_options).json()["data"]

    def catalogue_page(self, page: int) -> dict:
        return fetch(self.METADATA_API, params={"page": page}, **self.fetch_options).json()["data"]

    @staticmethod
    def page_url(dataset_id: str) -> str:
        return f"https://data.gov.sg/datasets/{dataset_id}/view"


class SingStat:
    name = "SingStat Table Builder"
    licence = "Department of Statistics Singapore; also published on data.gov.sg under the Singapore Open Data Licence"
    API = "https://tablebuilder.singstat.gov.sg/api/table/tabledata"

    def __init__(self, **fetch_options):
        self.fetch_options = fetch_options

    def download(self, table_id: str) -> bytes:
        return fetch(f"{self.API}/{table_id}", **self.fetch_options).content

    @staticmethod
    def page_url(table_id: str) -> str:
        return f"https://tablebuilder.singstat.gov.sg/table/TS/{table_id}"


class Internal:
    """Stands in for an agency's internal system. The workbook is synthetic, made by scripts/make_internal_mock.py."""
    name = "Internal (mock)"
    licence = "Synthetic data for demonstration; not an official statistic"

    def download(self, workbook: str) -> bytes:
        path = DATA_DIR / "internal" / workbook
        if not path.is_file():
            raise SourceUnavailable(f"internal workbook missing: {path.name}")
        return path.read_bytes()

    @staticmethod
    def page_url(workbook: str) -> str:
        return f"internal://{workbook}"


class Firms:
    """NASA FIRMS: fires detected by satellite. Its API needs a free map key (FIRMS_MAP_KEY), so it is used only to
    build the committed hotspot snapshot (scripts/make_hotspots.py), never while answering a question."""
    name = "NASA FIRMS"
    licence = ("NASA LANCE FIRMS, free to use with acknowledgement: data from LANCE FIRMS operated by NASA's Earth "
               "Science Data and Information System (ESDIS)")
    API = "https://firms.modaps.eosdis.nasa.gov/api"

    def __init__(self, map_key: str = ""):
        self.map_key = map_key

    def download(self, source_id: str) -> bytes:
        raise SourceUnavailable("hotspots are refreshed by scripts/make_hotspots.py, not while answering")

    def page_url(self, source_id: str) -> str:
        return "https://firms.modaps.eosdis.nasa.gov/"

    def get(self, path: str) -> str:
        """One API request. The key is part of the URL, so it is removed from any error before it is raised."""
        if not self.map_key:
            raise SourceUnavailable("FIRMS_MAP_KEY is not set: python3 scripts/set_keys.py FIRMS_MAP_KEY")
        try:
            return fetch(f"{self.API}/{path.format(key=self.map_key)}", timeout_s=180).text
        except SourceUnavailable as error:
            raise SourceUnavailable(str(error).replace(self.map_key, "<MAP_KEY>")) from None

    def area(self, product: str, bbox: str, start: str, days: int) -> str:
        """Hotspots in a box ("west,south,east,north") for `days` days from `start` (YYYY-MM-DD), as CSV."""
        return self.get(f"area/csv/{{key}}/{product}/{bbox}/{days}/{start}")


SOURCES = {"datagovsg": DataGovSg, "singstat": SingStat, "internal": Internal, "firms": Firms}
