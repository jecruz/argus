#!/usr/bin/env python3
"""Poll ~/.codex/state_5.sqlite every 10 minutes and cache usage data for local UIs.

Reads thread records from the Codex SQLite database and writes a JSON snapshot to
~/.claude/codex-usage.json. The SessionWidget desktop app reads this file to show
Codex usage alongside Claude usage. Any other tool can read the file directly.

"Today" window: threads with updated_at_ms > (now_ms - 86400 * 1000) — last 24 hours.

If the database does not exist (Codex never run, or different version), writes
{"active": false, "fetched_ms": <now>} and exits cleanly without error.

DB locking: Codex may hold a write lock while running. We open with check_same_thread=False
and timeout=5 so we wait briefly rather than failing immediately.

Run cadence: every 10 minutes via launchd (see install.sh).
Cache file: ~/.claude/codex-usage.json
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

HOME = os.path.expanduser("~")
DB_PATH = os.path.join(HOME, ".codex", "state_5.sqlite")
CACHE = os.path.join(HOME, ".claude", "codex-usage.json")
ACTIVE_THRESHOLD_MS = 12 * 60 * 1000   # 12 minutes in milliseconds
WINDOW_MS = 24 * 3600 * 1000           # 24-hour lookback window in milliseconds


# ---------- helpers ----------

def write_cache(d: dict):
    """Write cache dict to CACHE atomically with mode 0o600 (owner r/w only)."""
    d["fetched_ms"] = int(time.time() * 1000)
    cache_dir = os.path.dirname(CACHE)
    fd, tmp = tempfile.mkstemp(dir=cache_dir, prefix=".codex-usage-", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
    except:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover
            pass         # pragma: no cover
        raise
    os.replace(tmp, CACHE)


def query_db(now_ms: int) -> dict:
    """Query the Codex SQLite DB and return a usage snapshot dict.

    Returns a dict with keys: tokens_today, sessions_today, last_active_ms,
    top_model, model_breakdown, active. On any sqlite3 error, returns
    {"active": False} so write_cache can write a safe fallback.
    """
    cutoff_ms = now_ms - WINDOW_MS
    try:
        con = sqlite3.connect(
            DB_PATH,
            check_same_thread=False,
            timeout=5,
        )
    except sqlite3.OperationalError:
        return {"active": False}

    try:
        with con:
            cur = con.execute(
                """
                SELECT
                    COALESCE(SUM(tokens_used), 0) AS tokens_today,
                    COUNT(*) AS sessions_today,
                    MAX(updated_at_ms) AS last_active_ms
                FROM threads
                WHERE archived = 0
                  AND updated_at_ms > ?
                """,
                (cutoff_ms,),
            )
            row = cur.fetchone()
            tokens_today = int(row[0]) if row[0] is not None else 0
            sessions_today = int(row[1]) if row[1] is not None else 0
            last_active_ms = int(row[2]) if row[2] is not None else None

            cur2 = con.execute(
                """
                SELECT model, SUM(tokens_used) AS tokens, COUNT(*) AS sessions
                FROM threads
                WHERE archived = 0
                  AND updated_at_ms > ?
                  AND model IS NOT NULL AND model != ''
                GROUP BY model
                ORDER BY SUM(tokens_used) DESC
                """,
                (cutoff_ms,),
            )
            model_rows = cur2.fetchall()
    except sqlite3.OperationalError:
        return {"active": False}
    finally:
        con.close()

    model_breakdown: dict[str, dict] = {}
    top_model = None
    for i, (model, tokens, sessions) in enumerate(model_rows):
        model_breakdown[model] = {"tokens": int(tokens), "sessions": int(sessions)}
        if i == 0:
            top_model = model

    active = (
        last_active_ms is not None
        and (now_ms - last_active_ms) <= ACTIVE_THRESHOLD_MS
    )

    return {
        "tokens_today": tokens_today,
        "sessions_today": sessions_today,
        "last_active_ms": last_active_ms,
        "top_model": top_model,
        "model_breakdown": model_breakdown,
        "active": active,
    }


# ---------- main ----------

def main():
    """Poll DB_PATH and write a usage snapshot to CACHE."""
    now_ms = int(time.time() * 1000)

    if not os.path.exists(DB_PATH):
        write_cache({"active": False})
        return

    result = query_db(now_ms)
    write_cache(result)


if __name__ == "__main__":  # pragma: no cover
    main()
