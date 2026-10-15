"""The application database: one SQLite file (`var/app.sqlite`) holding the API's tables (conversations, runs,
events, LLM calls, reports; `api/db.py`) and the data layer's tables (cleaned datasets and raw downloads;
`data/database.py`). Each module declares its own tables; both open the file through `sqlite_engine`."""
from sqlalchemy import Engine, create_engine, event

from app.config import REPO_ROOT

DB_PATH = REPO_ROOT / "var" / "app.sqlite"


def sqlite_engine(url: str | None = None) -> Engine:
    """An engine for the database at `url` (default: the application database), in WAL mode."""
    if url is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{DB_PATH}"
    # A file database, one connection per thread. (A shared in-memory connection would let one thread's rollback
    # undo another thread's insert while a run is writing events.)
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(connection, _record):
        connection.execute("pragma journal_mode = wal")  # readers (WebSockets) never block the run writing
        connection.execute("pragma foreign_keys = on")

    return engine
