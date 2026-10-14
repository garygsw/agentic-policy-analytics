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


SOURCES = {"datagovsg": DataGovSg, "singstat": SingStat, "internal": Internal}
