"""Tests for codex_usage_poll.py.

Covers write_cache, query_db, and main(). All file I/O uses tmp_path so the
real ~/.claude/ directory is never touched. DB_PATH and CACHE are patched on
the module for each test.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Module import helper
# ---------------------------------------------------------------------------

def _load_module():
    """Import (or re-import) codex_usage_poll from the project root."""
    project_root = os.path.join(os.path.dirname(__file__), "..")
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    if "codex_usage_poll" in sys.modules:
        del sys.modules["codex_usage_poll"]
    import codex_usage_poll as m
    return m


def _make_db(db_path: str, rows: list[dict] | None = None) -> None:
    """Create a minimal Codex-style SQLite DB at db_path with optional rows.

    Each row dict may contain: model, tokens_used, updated_at_ms, archived.
    Defaults: archived=0, tokens_used=0, updated_at_ms=now_ms.
    """
    con = sqlite3.connect(db_path)
    con.execute(
        """
        CREATE TABLE threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT,
            tokens_used INTEGER DEFAULT 0,
            updated_at_ms INTEGER,
            archived INTEGER DEFAULT 0
        )
        """
    )
    now_ms = int(time.time() * 1000)
    for row in (rows or []):
        con.execute(
            "INSERT INTO threads (model, tokens_used, updated_at_ms, archived) VALUES (?, ?, ?, ?)",
            (
                row.get("model"),
                row.get("tokens_used", 0),
                row.get("updated_at_ms", now_ms),
                row.get("archived", 0),
            ),
        )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# write_cache
# ---------------------------------------------------------------------------

class TestWriteCache:
    def test_file_created_with_mode_0o600(self, tmp_path):
        m = _load_module()
        cache_file = tmp_path / "codex-usage.json"
        with patch.object(m, "CACHE", str(cache_file)):
            m.write_cache({"active": False})
        file_mode = stat.S_IMODE(os.stat(str(cache_file)).st_mode)
        assert file_mode == 0o600

    def test_file_contains_correct_json(self, tmp_path):
        m = _load_module()
        cache_file = tmp_path / "codex-usage.json"
        with patch.object(m, "CACHE", str(cache_file)):
            m.write_cache({"active": True, "tokens_today": 42})
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["active"] is True
        assert data["tokens_today"] == 42

    def test_fetched_ms_key_is_added(self, tmp_path):
        m = _load_module()
        cache_file = tmp_path / "codex-usage.json"
        before_ms = int(time.time() * 1000)
        with patch.object(m, "CACHE", str(cache_file)):
            m.write_cache({"active": False})
        after_ms = int(time.time() * 1000)
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert "fetched_ms" in data
        assert before_ms <= data["fetched_ms"] <= after_ms

    def test_temp_file_removed_when_write_fails(self, tmp_path):
        m = _load_module()
        cache_file = tmp_path / "codex-usage.json"
        with patch.object(m, "CACHE", str(cache_file)):
            with patch("os.fdopen", side_effect=OSError("write failed")):
                with pytest.raises(OSError, match="write failed"):
                    m.write_cache({"active": False})
        leftover = list(tmp_path.glob(".codex-usage-*.tmp"))
        assert leftover == []


# ---------------------------------------------------------------------------
# DB not found
# ---------------------------------------------------------------------------

class TestDbNotFound:
    def test_writes_active_false_when_db_missing(self, tmp_path):
        m = _load_module()
        cache_file = tmp_path / "codex-usage.json"
        missing_db = str(tmp_path / "nonexistent.sqlite")
        with (
            patch.object(m, "DB_PATH", missing_db),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["active"] is False
        assert "fetched_ms" in data

    def test_exits_cleanly_when_db_missing(self, tmp_path):
        m = _load_module()
        cache_file = tmp_path / "codex-usage.json"
        missing_db = str(tmp_path / "nonexistent.sqlite")
        with (
            patch.object(m, "DB_PATH", missing_db),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            # Should not raise
            m.main()


# ---------------------------------------------------------------------------
# Empty DB (no rows in last 24h)
# ---------------------------------------------------------------------------

class TestEmptyDb:
    def test_writes_zero_totals_and_active_false(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        _make_db(str(db_file), rows=[])
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["tokens_today"] == 0
        assert data["sessions_today"] == 0
        assert data["active"] is False

    def test_empty_model_breakdown_and_no_top_model(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        _make_db(str(db_file), rows=[])
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["model_breakdown"] == {}
        assert data["top_model"] is None


# ---------------------------------------------------------------------------
# Normal data — correct totals, breakdown, active
# ---------------------------------------------------------------------------

class TestNormalData:
    def _now_ms(self):
        return int(time.time() * 1000)

    def test_correct_token_and_session_totals(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        now_ms = self._now_ms()
        rows = [
            {"model": "gpt-5.5", "tokens_used": 100_000, "updated_at_ms": now_ms - 1000},
            {"model": "gpt-5.5", "tokens_used": 91_436_082, "updated_at_ms": now_ms - 2000},
            {"model": "o3", "tokens_used": 500, "updated_at_ms": now_ms - 3000},
        ]
        _make_db(str(db_file), rows=rows)
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["tokens_today"] == 91_536_582
        assert data["sessions_today"] == 3

    def test_correct_model_breakdown(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        now_ms = self._now_ms()
        rows = [
            {"model": "gpt-5.5", "tokens_used": 191_436_082, "updated_at_ms": now_ms - 1000},
            {"model": "gpt-5.5", "tokens_used": 0, "updated_at_ms": now_ms - 2000},
            {"model": "o3", "tokens_used": 500, "updated_at_ms": now_ms - 3000},
        ]
        _make_db(str(db_file), rows=rows)
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert "gpt-5.5" in data["model_breakdown"]
        assert data["model_breakdown"]["gpt-5.5"]["tokens"] == 191_436_082
        assert data["model_breakdown"]["gpt-5.5"]["sessions"] == 2
        assert "o3" in data["model_breakdown"]
        assert data["model_breakdown"]["o3"]["tokens"] == 500

    def test_top_model_is_highest_token_model(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        now_ms = self._now_ms()
        rows = [
            {"model": "gpt-5.5", "tokens_used": 191_436_082, "updated_at_ms": now_ms - 1000},
            {"model": "o3", "tokens_used": 500, "updated_at_ms": now_ms - 2000},
        ]
        _make_db(str(db_file), rows=rows)
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["top_model"] == "gpt-5.5"

    def test_active_true_when_last_active_is_recent(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        now_ms = self._now_ms()
        rows = [
            {"model": "gpt-5.5", "tokens_used": 100, "updated_at_ms": now_ms - 60_000},
        ]
        _make_db(str(db_file), rows=rows)
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["active"] is True

    def test_last_active_ms_is_max_updated_at(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        now_ms = self._now_ms()
        rows = [
            {"model": "gpt-5.5", "tokens_used": 10, "updated_at_ms": now_ms - 5000},
            {"model": "gpt-5.5", "tokens_used": 20, "updated_at_ms": now_ms - 1000},
        ]
        _make_db(str(db_file), rows=rows)
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["last_active_ms"] == now_ms - 1000

    def test_archived_threads_excluded(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        now_ms = self._now_ms()
        rows = [
            {"model": "gpt-5.5", "tokens_used": 999_999, "updated_at_ms": now_ms - 100, "archived": 1},
            {"model": "gpt-5.5", "tokens_used": 1, "updated_at_ms": now_ms - 200, "archived": 0},
        ]
        _make_db(str(db_file), rows=rows)
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["tokens_today"] == 1
        assert data["sessions_today"] == 1

    def test_threads_outside_24h_window_excluded(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        now_ms = self._now_ms()
        rows = [
            # Outside the 24h window
            {"model": "gpt-5.5", "tokens_used": 50_000, "updated_at_ms": now_ms - m.WINDOW_MS - 1000},
            # Inside the 24h window
            {"model": "gpt-5.5", "tokens_used": 7, "updated_at_ms": now_ms - 1000},
        ]
        _make_db(str(db_file), rows=rows)
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["tokens_today"] == 7
        assert data["sessions_today"] == 1


# ---------------------------------------------------------------------------
# Inactive — active=False when last_active_ms is older than ACTIVE_THRESHOLD_MS
# ---------------------------------------------------------------------------

class TestInactive:
    def test_active_false_when_last_activity_is_stale(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        now_ms = int(time.time() * 1000)
        # Last activity 13 minutes ago — beyond the 12-minute threshold
        rows = [
            {
                "model": "gpt-5.5",
                "tokens_used": 100,
                "updated_at_ms": now_ms - m.ACTIVE_THRESHOLD_MS - 60_000,
            }
        ]
        _make_db(str(db_file), rows=rows)
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["active"] is False

    def test_active_false_when_no_rows(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        _make_db(str(db_file), rows=[])
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["active"] is False


# ---------------------------------------------------------------------------
# DB locked — sqlite3.OperationalError on connect
# ---------------------------------------------------------------------------

class TestDbLocked:
    def test_handles_operational_error_on_connect_gracefully(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        # Create file so os.path.exists passes
        db_file.write_bytes(b"")
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
            patch("sqlite3.connect", side_effect=sqlite3.OperationalError("locked")),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["active"] is False

    def test_handles_operational_error_on_execute_gracefully(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        # Real DB so connect succeeds, but patch execute to fail
        _make_db(str(db_file), rows=[])
        original_connect = sqlite3.connect

        def _bad_connect(*args, **kwargs):
            con = original_connect(*args, **kwargs)
            con.execute("DROP TABLE threads")
            con.commit()
            return con

        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
            patch("sqlite3.connect", side_effect=_bad_connect),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["active"] is False


# ---------------------------------------------------------------------------
# main() integration — DB with real data → correct output file
# ---------------------------------------------------------------------------

class TestMainIntegration:
    def test_full_snapshot_matches_expected_schema(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        now_ms = int(time.time() * 1000)
        rows = [
            {"model": "gpt-5.5", "tokens_used": 191_436_082, "updated_at_ms": now_ms - 60_000},
            {"model": "gpt-5.5", "tokens_used": 0, "updated_at_ms": now_ms - 120_000},
            {"model": "o3", "tokens_used": 500, "updated_at_ms": now_ms - 180_000},
            {"model": "o3", "tokens_used": 200, "updated_at_ms": now_ms - 240_000},
        ]
        _make_db(str(db_file), rows=rows)
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))

        # All required keys present
        for key in ("fetched_ms", "tokens_today", "sessions_today", "last_active_ms",
                    "top_model", "model_breakdown", "active"):
            assert key in data, f"missing key: {key}"

        assert data["tokens_today"] == 191_436_782
        assert data["sessions_today"] == 4
        assert data["top_model"] == "gpt-5.5"
        assert data["active"] is True
        assert data["model_breakdown"]["gpt-5.5"]["tokens"] == 191_436_082
        assert data["model_breakdown"]["gpt-5.5"]["sessions"] == 2
        assert data["model_breakdown"]["o3"]["tokens"] == 700
        assert data["model_breakdown"]["o3"]["sessions"] == 2

    def test_output_file_has_mode_0o600(self, tmp_path):
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        _make_db(str(db_file), rows=[])
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        file_mode = stat.S_IMODE(os.stat(str(cache_file)).st_mode)
        assert file_mode == 0o600

    def test_null_model_rows_excluded_from_breakdown(self, tmp_path):
        """Rows with NULL or empty model are excluded from model_breakdown."""
        m = _load_module()
        db_file = tmp_path / "state_5.sqlite"
        cache_file = tmp_path / "codex-usage.json"
        now_ms = int(time.time() * 1000)
        rows = [
            {"model": None, "tokens_used": 9999, "updated_at_ms": now_ms - 1000},
            {"model": "", "tokens_used": 8888, "updated_at_ms": now_ms - 2000},
            {"model": "gpt-5.5", "tokens_used": 1, "updated_at_ms": now_ms - 3000},
        ]
        _make_db(str(db_file), rows=rows)
        with (
            patch.object(m, "DB_PATH", str(db_file)),
            patch.object(m, "CACHE", str(cache_file)),
        ):
            m.main()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        # NULL/empty model rows still count toward totals but not breakdown
        assert data["sessions_today"] == 3
        assert "gpt-5.5" in data["model_breakdown"]
        assert None not in data["model_breakdown"]
        assert "" not in data["model_breakdown"]
