#imdb_dataset.py
"""
Local IMDb ratings source, sourced from IMDb's own non-commercial datasets
(https://datasets.imdbws.com/title.ratings.tsv.gz) rather than MDBList.

Why this exists: every rating PostersPlus can weight — including the one
labelled "imdb" — currently comes from a single MDBList API call per title.
That is fine for the many sources MDBList aggregates (Letterboxd, Trakt,
Rotten Tomatoes, Metacritic, ...), which have no public bulk-download route.
IMDb is the one exception: IMDb itself publishes a free, no-key-required,
daily-refreshed TSV of every title's aggregate rating and vote count. An
operator who only cares about the IMDb score does not need an MDBList key
(or its rate limits) at all to get one.

This module downloads that TSV on a schedule, loads it into a small SQLite
table keyed on the IMDb id (tconst), and answers point lookups from it. It
never makes a per-title network call — the whole dataset is pulled in one
shot on a timer, matching the pattern digital_release.py uses for its own
periodic background sync.

Enabled with IMDB_DATASET_ENABLED=true. Fully inert (no download, no table,
lookups always return None) when disabled — the default.
"""
import asyncio
import gzip
import io
import logging
import os
import sqlite3
import threading
import time

import httpx

from config import (
    IMDB_DATASET_ENABLED,
    IMDB_DATASET_PATH,
    IMDB_DATASET_REFRESH_HOURS,
    IMDB_DATASET_MIN_VOTES,
)

logger = logging.getLogger(__name__)

_DATASET_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"

_local = threading.local()
_initialised = False
_last_refresh_ts: float | None = None
_last_refresh_rows: int = 0
_last_refresh_error: str | None = None


def is_enabled() -> bool:
    return bool(IMDB_DATASET_ENABLED)


def _get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(IMDB_DATASET_PATH) or ".", exist_ok=True)
        conn = sqlite3.connect(IMDB_DATASET_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS imdb_ratings (
                tconst  TEXT PRIMARY KEY,
                rating  REAL NOT NULL,
                votes   INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS imdb_ratings_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()
        _local.conn = conn
    return conn


def init_db() -> None:
    """Idempotent — safe to call once at startup even when disabled, so the
    file exists and later enabling the feature needs no migration step."""
    global _initialised
    if not is_enabled():
        return
    _get_db()
    _initialised = True


def status() -> dict:
    return {
        "enabled": is_enabled(),
        "path": IMDB_DATASET_PATH if is_enabled() else None,
        "refresh_hours": IMDB_DATASET_REFRESH_HOURS,
        "min_votes": IMDB_DATASET_MIN_VOTES,
        "last_refresh_unix": _last_refresh_ts,
        "last_refresh_rows": _last_refresh_rows,
        "last_refresh_error": _last_refresh_error,
        "row_count": row_count() if is_enabled() else 0,
    }


def row_count() -> int:
    if not is_enabled():
        return 0
    try:
        conn = _get_db()
        return conn.execute("SELECT COUNT(*) FROM imdb_ratings").fetchone()[0]
    except Exception:
        return 0


def get_rating(imdb_id: str | None) -> float | None:
    """Return the 0-10 average rating for *imdb_id*, or None if unknown /
    below IMDB_DATASET_MIN_VOTES / the feature is disabled.

    Synchronous — this is a single indexed SQLite lookup (sub-millisecond),
    not a network call, so it is safe to call inline from the request path.
    """
    if not is_enabled() or not imdb_id:
        return None
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT rating, votes FROM imdb_ratings WHERE tconst = ?",
            (imdb_id,),
        ).fetchone()
    except Exception as exc:
        logger.warning(f"IMDb dataset lookup failed for {imdb_id}: {exc}")
        return None
    if row is None:
        return None
    rating, votes = row
    if votes < IMDB_DATASET_MIN_VOTES:
        return None
    return float(rating)


async def refresh_dataset(client: httpx.AsyncClient) -> int:
    """Download and reload the full dataset. Returns the row count loaded.

    Runs the download+parse+bulk-insert off the event loop (it's a ~25 MB
    gzip download and a few million-row parse) so it never blocks request
    handling; only the final connection handoff happens on this thread.
    """
    global _last_refresh_ts, _last_refresh_rows, _last_refresh_error

    if not is_enabled():
        return 0

    try:
        resp = await client.get(_DATASET_URL, timeout=120.0)
        resp.raise_for_status()
        raw = resp.content
    except Exception as exc:
        _last_refresh_error = f"download failed: {exc}"
        logger.error(f"IMDb dataset download failed: {exc}")
        return 0

    def _parse_and_load() -> int:
        rows: list[tuple[str, float, int]] = []
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            text = io.TextIOWrapper(gz, encoding="utf-8")
            header = text.readline()  # tconst  averageRating  numVotes
            for line in text:
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 3:
                    continue
                tconst, rating_str, votes_str = parts
                try:
                    rating = float(rating_str)
                    votes = int(votes_str)
                except ValueError:
                    continue
                rows.append((tconst, rating, votes))

        conn = sqlite3.connect(IMDB_DATASET_PATH, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS imdb_ratings (
                    tconst  TEXT PRIMARY KEY,
                    rating  REAL NOT NULL,
                    votes   INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS imdb_ratings_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            # Load into a fresh table and swap it in, so concurrent readers on
            # other connections never see a half-populated table mid-refresh.
            conn.execute("DROP TABLE IF EXISTS imdb_ratings_new")
            conn.execute("""
                CREATE TABLE imdb_ratings_new (
                    tconst  TEXT PRIMARY KEY,
                    rating  REAL NOT NULL,
                    votes   INTEGER NOT NULL
                )
            """)
            conn.execute("BEGIN")
            conn.executemany(
                "INSERT INTO imdb_ratings_new (tconst, rating, votes) VALUES (?, ?, ?)",
                rows,
            )
            conn.execute("DROP TABLE imdb_ratings")
            conn.execute("ALTER TABLE imdb_ratings_new RENAME TO imdb_ratings")
            conn.execute(
                "INSERT OR REPLACE INTO imdb_ratings_meta (key, value) VALUES ('last_refresh', ?)",
                (str(int(time.time())),),
            )
            conn.commit()
        finally:
            conn.close()
        return len(rows)

    try:
        loop = asyncio.get_running_loop()
        count = await loop.run_in_executor(None, _parse_and_load)
    except Exception as exc:
        _last_refresh_error = f"parse/load failed: {exc}"
        logger.error(f"IMDb dataset parse/load failed: {exc}")
        return 0

    # Any per-thread connections opened before the swap point at a now-stale
    # schema object; drop the cached handle on this thread so the next lookup
    # reconnects and sees the new table.
    _local.conn = None

    _last_refresh_ts = time.time()
    _last_refresh_rows = count
    _last_refresh_error = None
    logger.info(f"IMDb dataset refreshed: {count} titles loaded from {_DATASET_URL}")
    return count


async def imdb_dataset_refresh_loop(client: httpx.AsyncClient) -> None:
    """Background task: refresh shortly after startup, then every
    IMDB_DATASET_REFRESH_HOURS. IMDb regenerates this file once a day, so
    refreshing more often than that buys nothing."""
    if not is_enabled():
        return
    await asyncio.sleep(30)  # let the service finish warming up first
    while True:
        try:
            await refresh_dataset(client)
        except Exception as exc:
            logger.error(f"IMDb dataset refresh loop error: {exc}")
        await asyncio.sleep(max(1, IMDB_DATASET_REFRESH_HOURS) * 3600)
