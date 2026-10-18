"""The ingest service: crawls agency newsrooms and refreshes the dataset catalogue on a cron schedule.

    uv run python -m app.rag.scheduler          # run on INGEST_CRON (default 03:00 daily, Singapore time)
    uv run python -m app.rag.scheduler --now    # one run immediately, then exit

Runs as its own process (its own container in Docker Compose), so crawling never slows the API.
"""
import argparse
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.data import catalogue
from app.observability import setup_logging
from app.rag.embed import get_embedder
from app.rag.ingest import index_catalogue, ingest_source
from app.rag.search import open_index
from app.rag.sources import DOCUMENT_SOURCES

TIMEZONE = "Asia/Singapore"
log = logging.getLogger("ingest")


def run_once() -> list[dict]:
    """One full refresh: every newsroom, then the data.gov.sg catalogue. A failing source does not stop the rest."""
    settings, index, embedder = get_settings(), open_index(), get_embedder()
    runs = []
    for source in DOCUMENT_SOURCES.values():
        try:
            runs.append(ingest_source(source, index, embedder, since=settings.ingest_since,
                                      limit=settings.ingest_limit_per_source, log=log.warning))
        except Exception:  # noqa: BLE001 - one site being down must not block the others
            log.exception("ingest failed for %s", source.key)
    try:
        catalogue.refresh()
        runs.append(index_catalogue(index, embedder))
    except Exception:  # noqa: BLE001
        log.exception("catalogue refresh failed")
    for run in runs:
        log.info("%(source)s: %(seen)s seen, %(new)s new, %(updated)s updated, %(failed)s failed", run)
    return runs


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--now", action="store_true", help="run once immediately and exit")
    args = parser.parse_args()
    if args.now:
        run_once()
        return
    cron = get_settings().ingest_cron
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    scheduler.add_job(run_once, CronTrigger.from_crontab(cron, timezone=TIMEZONE), id="ingest",
                      max_instances=1, coalesce=True)
    log.info("ingest scheduled: '%s' (%s)", cron, TIMEZONE)
    scheduler.start()


if __name__ == "__main__":
    main()
