"""Administration from the backend: list deleted conversations and restore them.

    uv run python -m app.admin deleted            # deleted conversations, newest first
    uv run python -m app.admin restore THREAD_ID  # bring one back to the history list

Deleting a conversation in the UI is a soft delete (it sets `deleted_at`); nothing is removed. In Docker, run these
inside the API container (`docker compose exec api python -m app.admin deleted`), never against the database
file from the host.
"""
import argparse

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.api.db import Run, Thread, make_session_factory
from app.api.runs import iso


def deleted_threads(sessions: sessionmaker) -> list[dict]:
    with sessions() as session:
        threads = session.scalars(select(Thread).where(Thread.deleted_at.is_not(None))
                                  .order_by(Thread.deleted_at.desc())).all()
        return [{"id": t.id, "title": t.title, "deleted_at": iso(t.deleted_at),
                 "runs": session.scalar(select(func.count()).select_from(Run).where(Run.thread_id == t.id))}
                for t in threads]


def restore(sessions: sessionmaker, thread_id: str) -> bool:
    """Undo a delete. Returns False if there is no deleted conversation with that ID."""
    with sessions() as session:
        thread = session.get(Thread, thread_id)
        if thread is None or thread.deleted_at is None:
            return False
        thread.deleted_at = None
        session.commit()
        return True


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.admin", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("deleted", help="list deleted conversations")
    commands.add_parser("restore", help="restore a deleted conversation").add_argument("thread_id")
    args = parser.parse_args()
    sessions = make_session_factory()
    if args.command == "deleted":
        rows = deleted_threads(sessions)
        for row in rows:
            print(f"{row['id']}  {row['deleted_at']}  {row['runs']} run(s)  {row['title']}")
        print(f"{len(rows)} deleted conversation(s)")
    elif restore(sessions, args.thread_id):
        print(f"Restored {args.thread_id}")
    else:
        raise SystemExit(f"No deleted conversation with ID {args.thread_id}")


if __name__ == "__main__":
    main()
