"""The document index: one SQLite file with documents, passages, a BM25 keyword index (FTS5) and a vector index
(sqlite-vec). Built by the ingest service, read by search. WAL mode lets both run at the same time.
"""
import re
import sqlite3
from pathlib import Path

import sqlite_vec

from app.config import REPO_ROOT
from app.rag.chunking import Chunk

INDEX_PATH = REPO_ROOT / "var" / "rag" / "index.sqlite"
STOPWORDS = {"the", "of", "and", "in", "by", "for", "to", "a", "an", "on", "at", "from", "with", "what", "how",
             "is", "are", "was", "were", "be", "did", "does", "do", "about", "between", "this", "that"}

SCHEMA = """
create table if not exists meta(key text primary key, value text);
create table if not exists documents(
    id integer primary key, url text unique not null, source text not null, agency text, doc_type text,
    title text, published text, lastmod text, content_hash text, fetched_at text);
create table if not exists chunks(
    id integer primary key, document_id integer not null references documents(id), position integer,
    header text, text text, tokens integer);
create index if not exists chunks_by_document on chunks(document_id);
create virtual table if not exists chunks_fts using fts5(header, text, tokenize = 'porter unicode61');
create table if not exists ingest_runs(
    id integer primary key, source text, started text, finished text, seen integer, new integer, updated integer,
    unchanged integer, skipped integer, failed integer, notes text);
"""


class IndexModelMismatch(RuntimeError):
    """The index was built with a different embedding model; searching it would compare unlike vectors."""


def fts_query(text: str) -> str:
    """Free text -> a safe FTS5 query: quoted words joined with OR (punctuation would be FTS5 syntax)."""
    words = [w for w in re.findall(r"\w+", text.lower()) if w not in STOPWORDS and len(w) > 1]
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(words))


class DocumentIndex:
    def __init__(self, path: Path = INDEX_PATH, model: str = "", dim: int = 0):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self.db.execute("pragma journal_mode = wal")
        self.db.executescript(SCHEMA)
        stored = dict(self.db.execute("select key, value from meta").fetchall())
        if stored and model and (stored.get("model"), int(stored.get("dim", 0))) != (model, dim):
            raise IndexModelMismatch(f"index built with {stored.get('model')} ({stored.get('dim')} dims), "
                                     f"not {model}; rebuild it with the ingest command")
        if not stored and model:
            self.db.executemany("insert into meta values (?, ?)", [("model", model), ("dim", str(dim))])
            self.db.commit()
        self.dim = int(stored.get("dim", dim) or dim)
        if self.dim:
            self.db.execute(f"create virtual table if not exists chunks_vec using vec0(embedding float[{self.dim}])")

    # -- writing ------------------------------------------------------------------------------------------------
    def document(self, url: str) -> sqlite3.Row | None:
        return self.db.execute("select * from documents where url = ?", (url,)).fetchone()

    def upsert(self, doc: dict, chunks: list[Chunk], vectors: list[list[float]]) -> int:
        """Insert or replace a document and all its passages, in one transaction."""
        with self.db:
            existing = self.document(doc["url"])
            if existing:
                self._delete_chunks(existing["id"])
                self.db.execute("""update documents set source=?, agency=?, doc_type=?, title=?, published=?,
                                   lastmod=?, content_hash=?, fetched_at=? where id=?""",
                                (doc["source"], doc["agency"], doc["doc_type"], doc["title"], doc["published"],
                                 doc["lastmod"], doc["content_hash"], doc["fetched_at"], existing["id"]))
                doc_id = existing["id"]
            else:
                doc_id = self.db.execute("""insert into documents(url, source, agency, doc_type, title, published,
                                            lastmod, content_hash, fetched_at) values (?,?,?,?,?,?,?,?,?)""",
                                         (doc["url"], doc["source"], doc["agency"], doc["doc_type"], doc["title"],
                                          doc["published"], doc["lastmod"], doc["content_hash"],
                                          doc["fetched_at"])).lastrowid
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk_id = self.db.execute("insert into chunks(document_id, position, header, text, tokens) "
                                           "values (?,?,?,?,?)",
                                           (doc_id, chunk.position, chunk.header, chunk.text, chunk.tokens)).lastrowid
                self.db.execute("insert into chunks_fts(rowid, header, text) values (?,?,?)",
                                (chunk_id, chunk.header, chunk.text))
                self.db.execute("insert into chunks_vec(rowid, embedding) values (?,?)",
                                (chunk_id, sqlite_vec.serialize_float32(vector)))
        return doc_id

    def touch(self, url: str, lastmod: str) -> None:
        with self.db:
            self.db.execute("update documents set lastmod = ? where url = ?", (lastmod, url))

    def _delete_chunks(self, doc_id: int) -> None:
        ids = [r[0] for r in self.db.execute("select id from chunks where document_id = ?", (doc_id,))]
        for chunk_id in ids:
            self.db.execute("delete from chunks_fts where rowid = ?", (chunk_id,))
            self.db.execute("delete from chunks_vec where rowid = ?", (chunk_id,))
        self.db.execute("delete from chunks where document_id = ?", (doc_id,))

    def record_run(self, run: dict) -> None:
        with self.db:
            self.db.execute("insert into ingest_runs(source, started, finished, seen, new, updated, unchanged, "
                            "skipped, failed, notes) values (:source, :started, :finished, :seen, :new, :updated, "
                            ":unchanged, :skipped, :failed, :notes)", run)

    # -- reading ------------------------------------------------------------------------------------------------
    def _filters(self, source: str | None, since: str | None, datasets: bool) -> tuple[str, list]:
        sql = " and d.doc_type = 'dataset'" if datasets else " and d.doc_type != 'dataset'"
        params: list = []
        if source:
            sql += " and d.source = ?"
            params.append(source)
        if since:
            sql += " and d.published >= ?"
            params.append(since)
        return sql, params

    def keyword(self, query: str, limit: int, source: str | None = None, since: str | None = None,
                datasets: bool = False) -> list[int]:
        """Chunk IDs by BM25 relevance (FTS5's bm25(): lower is better)."""
        match = fts_query(query)
        if not match:
            return []
        where, params = self._filters(source, since, datasets)
        rows = self.db.execute(f"""select c.id from chunks_fts join chunks c on c.id = chunks_fts.rowid
                                   join documents d on d.id = c.document_id
                                   where chunks_fts match ? {where} order by bm25(chunks_fts) limit ?""",
                               [match, *params, limit])
        return [r[0] for r in rows]

    def nearest(self, vector: list[float], limit: int, source: str | None = None, since: str | None = None,
                datasets: bool = False) -> list[int]:
        """Chunk IDs by vector distance. Filters apply after the nearest-neighbour search, so it over-fetches."""
        rows = self.db.execute("select rowid from chunks_vec where embedding match ? and k = ? order by distance",
                               (sqlite_vec.serialize_float32(vector), limit * 4)).fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            return []
        where, params = self._filters(source, since, datasets)
        marks = ",".join("?" * len(ids))
        keep = {r[0] for r in self.db.execute(f"""select c.id from chunks c join documents d on d.id = c.document_id
                                                  where c.id in ({marks}) {where}""", [*ids, *params])}
        return [i for i in ids if i in keep][:limit]

    def passages(self, chunk_ids: list[int]) -> dict[int, sqlite3.Row]:
        if not chunk_ids:
            return {}
        marks = ",".join("?" * len(chunk_ids))
        rows = self.db.execute(f"""select c.id, c.header, c.text, d.url, d.title, d.agency, d.source, d.doc_type,
                                   d.published from chunks c join documents d on d.id = c.document_id
                                   where c.id in ({marks})""", chunk_ids)
        return {r["id"]: r for r in rows}

    def stats(self) -> dict:
        by_source = dict(self.db.execute("select source, count(*) from documents group by source").fetchall())
        chunks = self.db.execute("select count(*) from chunks").fetchone()[0]
        last = self.db.execute("select * from ingest_runs order by id desc limit 1").fetchone()
        return {"documents": by_source, "chunks": chunks, "last_run": dict(last) if last else None}
