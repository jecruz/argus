"""Tests for session_usage_poll.py.

Covers load_cache, write_cache, parse_usage, transcript_block,
latest_activity_mtime, oauth_token, probe_headers, and main().

All file I/O uses tmp_path so the real ~/.claude/ directory is never touched.
Module-level CACHE and PROJECTS constants are patched via unittest.mock.patch.
"""

from __future__ import annotations

import importlib
import json
import os
import stat
import sys
import time
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Module import helper
# ---------------------------------------------------------------------------

def _load_module(monkeypatch=None):
    """Import (or re-import) session_usage_poll from the project root."""
    project_root = os.path.join(os.path.dirname(__file__), "..")
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    # Force a fresh import so patches applied before the call take effect.
    if "session_usage_poll" in sys.modules:
        del sys.modules["session_usage_poll"]
    import session_usage_poll as m
    return m


# ---------------------------------------------------------------------------
# load_cache
# ---------------------------------------------------------------------------

class TestLoadCache:
    def test_returns_empty_dict_on_missing_file(self, tmp_path):
        m = _load_module()
        missing = str(tmp_path / "does_not_exist.json")
        with patch.object(m, "CACHE", missing):
            result = m.load_cache()
        assert result == {}

    def test_returns_empty_dict_on_invalid_json(self, tmp_path):
        m = _load_module()
        cache_file = tmp_path / "session-usage.json"
        cache_file.write_text("not valid json", encoding="utf-8")
        with patch.object(m, "CACHE", str(cache_file)):
            result = m.load_cache()
        assert result == {}

    def test_returns_dict_on_valid_json(self, tmp_path):
        m = _load_module()
        cache_file = tmp_path / "session-usage.json"
        payload = {"reset5h_ms": 1234567890000, "util5h": 0.42}
        cache_file.write_text(json.dumps(payload), encoding="utf-8")
        with patch.object(m, "CACHE", str(cache_file)):
            result = m.load_cache()
        assert result == payload


# ---------------------------------------------------------------------------
# write_cache
# ---------------------------------------------------------------------------

class TestWriteCache:
    def test_file_created_with_mode_0o600(self, tmp_path):
        m = _load_module()
        cache_file = tmp_path / "session-usage.json"
        with patch.object(m, "CACHE", str(cache_file)):
            m.write_cache({"source": "api"})
        file_mode = stat.S_IMODE(os.stat(str(cache_file)).st_mode)
        assert file_mode == 0o600

    def test_file_contains_correct_json(self, tmp_path):
        m = _load_module()
        cache_file = tmp_path / "session-usage.json"
        with patch.object(m, "CACHE", str(cache_file)):
            m.write_cache({"source": "api", "util5h": 0.5})
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["source"] == "api"
        assert data["util5h"] == 0.5

    def test_fetched_ms_key_is_added(self, tmp_path):
        m = _load_module()
        cache_file = tmp_path / "session-usage.json"
        before_ms = int(time.time() * 1000)
        with patch.object(m, "CACHE", str(cache_file)):
            m.write_cache({"source": "api"})
        after_ms = int(time.time() * 1000)
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert "fetched_ms" in data
        assert before_ms <= data["fetched_ms"] <= after_ms


# ---------------------------------------------------------------------------
# parse_usage
# ---------------------------------------------------------------------------

class TestParseUsage:
    def test_returns_none_when_5h_reset_header_absent(self):
        m = _load_module()
        result = m.parse_usage({"some-other-header": "value"})
        assert result is None

    def test_returns_correct_dict_when_all_headers_present(self):
        m = _load_module()
        headers = {
            "anthropic-ratelimit-unified-5h-reset": "1750000000",
            "anthropic-ratelimit-unified-5h-utilization": "0.35",
            "anthropic-ratelimit-unified-5h-status": "normal",
            "anthropic-ratelimit-unified-7d-reset": "1750100000",
            "anthropic-ratelimit-unified-7d-utilization": "0.12",
            "anthropic-ratelimit-unified-7d-status": "normal",
        }
        result = m.parse_usage(headers)
        assert result is not None
        assert result["reset5h_ms"] == 1750000000 * 1000
        assert result["util5h"] == pytest.approx(0.35)
        assert result["status5h"] == "normal"
        assert result["reset7d_ms"] == 1750100000 * 1000
        assert result["util7d"] == pytest.approx(0.12)
        assert result["status7d"] == "normal"

    def test_handles_missing_optional_7d_fields_gracefully(self):
        m = _load_module()
        headers = {
            "anthropic-ratelimit-unified-5h-reset": "1750000000",
        }
        result = m.parse_usage(headers)
        assert result is not None
        assert result["reset5h_ms"] == 1750000000 * 1000
        assert result["util5h"] is None
        assert result["status5h"] is None
        assert result["reset7d_ms"] is None
        assert result["util7d"] is None
        assert result["status7d"] is None

    def test_header_keys_are_case_insensitive(self):
        m = _load_module()
        headers = {
            "Anthropic-Ratelimit-Unified-5h-Reset": "1750000000",
            "Anthropic-Ratelimit-Unified-5h-Utilization": "0.9",
        }
        result = m.parse_usage(headers)
        assert result is not None
        assert result["reset5h_ms"] == 1750000000 * 1000


# ---------------------------------------------------------------------------
# transcript_block
# ---------------------------------------------------------------------------

class TestTranscriptBlock:
    def test_returns_none_when_no_jsonl_files(self, tmp_path):
        m = _load_module()
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.transcript_block(time.time())
        assert result is None

    def test_returns_start_end_for_single_timestamp_file(self, tmp_path):
        m = _load_module()
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()
        now_s = time.time()
        # Write a JSONL with one recent timestamp
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now_s - 60))
        (proj_dir / "session.jsonl").write_text(
            json.dumps({"type": "message", "timestamp": ts}) + "\n",
            encoding="utf-8",
        )
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.transcript_block(now_s)
        assert result is not None
        start_ms, end_ms = result
        assert end_ms == start_ms + m.WINDOW_MS
        assert start_ms < end_ms

    def test_returns_latest_block_when_activity_spans_more_than_5h(self, tmp_path):
        m = _load_module()
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()
        now_s = time.time()
        # Two timestamps: one 6 hours ago, one 30 minutes ago
        ts_old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now_s - 6 * 3600))
        ts_new = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now_s - 30 * 60))
        lines = (
            json.dumps({"type": "message", "timestamp": ts_old}) + "\n"
            + json.dumps({"type": "message", "timestamp": ts_new}) + "\n"
        )
        (proj_dir / "session.jsonl").write_text(lines, encoding="utf-8")
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.transcript_block(now_s)
        assert result is not None
        start_ms, end_ms = result
        # The block must be anchored at the newer timestamp
        expected_start_ms = int((now_s - 30 * 60) * 1000)
        assert abs(start_ms - expected_start_ms) < 2000  # within 2s tolerance


# ---------------------------------------------------------------------------
# latest_activity_mtime
# ---------------------------------------------------------------------------

class TestLatestActivityMtime:
    def test_returns_zero_when_no_files(self, tmp_path):
        m = _load_module()
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.latest_activity_mtime()
        assert result == 0.0

    def test_returns_newest_mtime(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        f1 = proj / "a.jsonl"
        f2 = proj / "b.jsonl"
        f1.write_text("{}", encoding="utf-8")
        f2.write_text("{}", encoding="utf-8")
        # Set distinct mtimes
        os.utime(str(f1), (1_000_000, 1_000_000))
        os.utime(str(f2), (2_000_000, 2_000_000))
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.latest_activity_mtime()
        assert result == pytest.approx(2_000_000.0)

    def test_skips_agent_prefixed_files(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        agent_file = proj / "agent-abc.jsonl"
        agent_file.write_text("{}", encoding="utf-8")
        os.utime(str(agent_file), (9_999_999, 9_999_999))
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.latest_activity_mtime()
        # agent- file must be skipped; no other files → 0.0
        assert result == 0.0


# ---------------------------------------------------------------------------
# oauth_token
# ---------------------------------------------------------------------------

class TestOauthToken:
    def test_returns_none_on_subprocess_failure(self):
        m = _load_module()
        with patch("subprocess.run", side_effect=Exception("keychain error")):
            result = m.oauth_token()
        assert result is None

    def test_returns_access_token_from_valid_keychain_json(self):
        m = _load_module()
        payload = json.dumps({"accessToken": "tok_abc123"})
        mock_result = MagicMock()
        mock_result.stdout = payload
        with patch("subprocess.run", return_value=mock_result):
            result = m.oauth_token()
        assert result == "tok_abc123"

    def test_returns_none_when_json_has_no_access_token_field(self):
        m = _load_module()
        payload = json.dumps({"someOtherKey": "value"})
        mock_result = MagicMock()
        mock_result.stdout = payload
        with patch("subprocess.run", return_value=mock_result):
            result = m.oauth_token()
        assert result is None

    def test_returns_none_on_invalid_json(self):
        m = _load_module()
        mock_result = MagicMock()
        mock_result.stdout = "not valid json"
        with patch("subprocess.run", return_value=mock_result):
            result = m.oauth_token()
        assert result is None

    def test_finds_token_in_nested_dict(self):
        m = _load_module()
        payload = json.dumps({"oauth": {"accessToken": "nested_tok_xyz"}})
        mock_result = MagicMock()
        mock_result.stdout = payload
        with patch("subprocess.run", return_value=mock_result):
            result = m.oauth_token()
        assert result == "nested_tok_xyz"

    def test_returns_token_for_access_token_key_variant(self):
        m = _load_module()
        payload = json.dumps({"access_token": "tok_underscore"})
        mock_result = MagicMock()
        mock_result.stdout = payload
        with patch("subprocess.run", return_value=mock_result):
            result = m.oauth_token()
        assert result == "tok_underscore"


# ---------------------------------------------------------------------------
# probe_headers
# ---------------------------------------------------------------------------

class TestProbeHeaders:
    def test_returns_headers_on_200(self):
        m = _load_module()
        fake_resp = MagicMock()
        fake_resp.headers = {"anthropic-ratelimit-unified-5h-reset": "1750000000"}
        with patch("urllib.request.urlopen", return_value=fake_resp):
            result = m.probe_headers("tok_abc")
        assert result == {"anthropic-ratelimit-unified-5h-reset": "1750000000"}

    def test_returns_headers_on_http_error_with_rate_limit_header(self):
        m = _load_module()
        err = urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages",
            code=429,
            msg="Too Many Requests",
            hdrs={"anthropic-ratelimit-unified-5h-reset": "1750000000"},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
            result = m.probe_headers("tok_abc")
        assert result is not None
        assert "anthropic-ratelimit-unified-5h-reset" in result

    def test_returns_none_on_http_error_without_rate_limit_header(self):
        m = _load_module()
        err = urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages",
            code=401,
            msg="Unauthorized",
            hdrs={"content-type": "application/json"},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
            result = m.probe_headers("tok_abc")
        assert result is None

    def test_returns_none_on_network_exception(self):
        m = _load_module()
        with patch("urllib.request.urlopen", side_effect=OSError("network error")):
            result = m.probe_headers("tok_abc")
        assert result is None


# ---------------------------------------------------------------------------
# transcript_block — error / edge branches
# ---------------------------------------------------------------------------

class TestTranscriptBlockEdgeCases:
    def test_skips_agent_prefixed_files(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        now_s = time.time()
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now_s - 60))
        (proj / "agent-sub.jsonl").write_text(
            json.dumps({"timestamp": ts}) + "\n", encoding="utf-8"
        )
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.transcript_block(now_s)
        assert result is None

    def test_skips_lines_with_invalid_json(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        now_s = time.time()
        (proj / "session.jsonl").write_text("not-json\n", encoding="utf-8")
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.transcript_block(now_s)
        assert result is None

    def test_skips_lines_with_missing_timestamp(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        now_s = time.time()
        (proj / "session.jsonl").write_text(
            json.dumps({"type": "no_ts_here"}) + "\n", encoding="utf-8"
        )
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.transcript_block(now_s)
        assert result is None

    def test_skips_lines_with_invalid_iso_timestamp(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        now_s = time.time()
        (proj / "session.jsonl").write_text(
            json.dumps({"timestamp": "not-a-date"}) + "\n", encoding="utf-8"
        )
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.transcript_block(now_s)
        assert result is None

    def test_returns_none_when_block_already_expired(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        now_s = time.time()
        # Timestamp from 6 hours ago — 5h window would have already expired
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now_s - 6 * 3600))
        (proj / "session.jsonl").write_text(
            json.dumps({"timestamp": ts}) + "\n", encoding="utf-8"
        )
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.transcript_block(now_s)
        assert result is None

    def test_skips_file_older_than_lookback_window(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        now_s = time.time()
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now_s - 60))
        f = proj / "old_session.jsonl"
        f.write_text(json.dumps({"timestamp": ts}) + "\n", encoding="utf-8")
        # Set mtime older than the 36h LOOKBACK_S threshold so the file is skipped.
        old_mtime = now_s - (m.LOOKBACK_S + 3600)
        os.utime(str(f), (old_mtime, old_mtime))
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.transcript_block(now_s)
        assert result is None

    def test_skips_file_on_getmtime_oserror_in_inner_loop(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "session.jsonl").write_text("{}", encoding="utf-8")
        now_s = time.time()
        with (
            patch.object(m, "PROJECTS", str(tmp_path)),
            patch("os.path.getmtime", side_effect=OSError("denied")),
        ):
            result = m.transcript_block(now_s)
        assert result is None

    def test_skips_line_with_timestamp_key_but_invalid_json(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        now_s = time.time()
        # Contains the literal string "timestamp" so it passes the fast check,
        # but is not valid JSON so json.loads must raise JSONDecodeError.
        (proj / "session.jsonl").write_text(
            '"timestamp": broken\n', encoding="utf-8"
        )
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.transcript_block(now_s)
        assert result is None

    def test_skips_line_with_null_timestamp_value(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        now_s = time.time()
        (proj / "session.jsonl").write_text(
            json.dumps({"timestamp": None}) + "\n", encoding="utf-8"
        )
        with patch.object(m, "PROJECTS", str(tmp_path)):
            result = m.transcript_block(now_s)
        assert result is None

    def test_skips_file_on_open_oserror(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        now_s = time.time()
        (proj / "session.jsonl").write_text("{}", encoding="utf-8")
        with (
            patch.object(m, "PROJECTS", str(tmp_path)),
            patch("builtins.open", side_effect=OSError("permission denied")),
        ):
            result = m.transcript_block(now_s)
        assert result is None


# ---------------------------------------------------------------------------
# write_cache — exception cleanup branch
# ---------------------------------------------------------------------------

class TestWriteCacheCleanup:
    def test_temp_file_removed_when_write_fails(self, tmp_path):
        m = _load_module()
        cache_file = tmp_path / "session-usage.json"
        with patch.object(m, "CACHE", str(cache_file)):
            with patch("os.fdopen", side_effect=OSError("write failed")):
                with pytest.raises(OSError, match="write failed"):
                    m.write_cache({"source": "api"})
        # The temp file must have been cleaned up on failure
        leftover = list(tmp_path.glob(".session-usage-*.tmp"))
        assert leftover == []


# ---------------------------------------------------------------------------
# latest_activity_mtime — OSError branch
# ---------------------------------------------------------------------------

class TestLatestActivityMtimeOsError:
    def test_skips_file_on_getmtime_oserror(self, tmp_path):
        m = _load_module()
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "session.jsonl").write_text("{}", encoding="utf-8")
        with patch.object(m, "PROJECTS", str(tmp_path)):
            with patch("os.path.getmtime", side_effect=OSError("permission denied")):
                result = m.latest_activity_mtime()
        assert result == 0.0


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------

class TestMain:
    def _patch_env(self, m, tmp_path, now_s, recent=True, prev=None):
        """Return a dict of patches needed for main() to run without side effects."""
        cache_file = tmp_path / "session-usage.json"
        if prev is not None:
            cache_file.write_text(json.dumps(prev), encoding="utf-8")
        proj_dir = tmp_path / "projects"
        proj_dir.mkdir()
        activity_mtime = now_s - 60 if recent else now_s - 99999
        return {
            "CACHE": str(cache_file),
            "PROJECTS": str(proj_dir),
            "_activity_mtime": activity_mtime,
            "_cache_file": cache_file,
        }

    def test_recent_activity_successful_probe_writes_api_source(self, tmp_path):
        m = _load_module()
        now_s = time.time()
        now_ms = int(now_s * 1000)
        reset_ms = now_ms + 3_600_000
        usage = {
            "reset5h_ms": reset_ms,
            "util5h": 0.4,
            "status5h": "normal",
            "reset7d_ms": None,
            "util7d": None,
            "status7d": None,
        }
        cache_file = tmp_path / "session-usage.json"
        with (
            patch.object(m, "CACHE", str(cache_file)),
            patch.object(m, "PROJECTS", str(tmp_path / "projects")),
            patch.object(m, "latest_activity_mtime", return_value=now_s - 60),
            patch.object(m, "oauth_token", return_value="tok_abc"),
            patch.object(m, "probe_headers", return_value={}),
            patch.object(m, "parse_usage", return_value=usage),
        ):
            m.main()
        data = json.loads(cache_file.read_text())
        assert data["source"] == "api"
        assert data["reset5h_ms"] == reset_ms

    def test_recent_activity_failed_probe_falls_back_to_prior_cache(self, tmp_path):
        m = _load_module()
        now_s = time.time()
        now_ms = int(now_s * 1000)
        prior_reset = now_ms + 1_800_000
        prior = {"source": "api", "reset5h_ms": prior_reset, "util5h": 0.3, "fetched_ms": now_ms - 600_000}
        cache_file = tmp_path / "session-usage.json"
        cache_file.write_text(json.dumps(prior), encoding="utf-8")
        with (
            patch.object(m, "CACHE", str(cache_file)),
            patch.object(m, "PROJECTS", str(tmp_path / "projects")),
            patch.object(m, "latest_activity_mtime", return_value=now_s - 60),
            patch.object(m, "oauth_token", return_value=None),
        ):
            m.main()
        data = json.loads(cache_file.read_text())
        assert data["active"] is True
        assert data["reset5h_ms"] == prior_reset

    def test_recent_activity_no_probe_no_cache_uses_transcript_block(self, tmp_path):
        m = _load_module()
        now_s = time.time()
        now_ms = int(now_s * 1000)
        blk = (now_ms - 1_800_000, now_ms + 1_800_000)
        cache_file = tmp_path / "session-usage.json"
        with (
            patch.object(m, "CACHE", str(cache_file)),
            patch.object(m, "PROJECTS", str(tmp_path / "projects")),
            patch.object(m, "latest_activity_mtime", return_value=now_s - 60),
            patch.object(m, "oauth_token", return_value=None),
            patch.object(m, "transcript_block", return_value=blk),
        ):
            m.main()
        data = json.loads(cache_file.read_text())
        assert data["source"] == "transcript"
        assert data["active"] is True
        assert data["reset5h_ms"] == blk[1]

    def test_recent_activity_no_probe_no_cache_no_transcript_writes_inactive(self, tmp_path):
        m = _load_module()
        now_s = time.time()
        cache_file = tmp_path / "session-usage.json"
        with (
            patch.object(m, "CACHE", str(cache_file)),
            patch.object(m, "PROJECTS", str(tmp_path / "projects")),
            patch.object(m, "latest_activity_mtime", return_value=now_s - 60),
            patch.object(m, "oauth_token", return_value=None),
            patch.object(m, "transcript_block", return_value=None),
        ):
            m.main()
        data = json.loads(cache_file.read_text())
        assert data["source"] == "transcript"
        assert data["active"] is False
        assert data["reset5h_ms"] is None

    def test_idle_keeps_valid_prior_reset(self, tmp_path):
        m = _load_module()
        now_s = time.time()
        now_ms = int(now_s * 1000)
        prior_reset = now_ms + 3_600_000
        prior = {"source": "api", "reset5h_ms": prior_reset, "util5h": 0.2, "fetched_ms": now_ms - 900_000}
        cache_file = tmp_path / "session-usage.json"
        cache_file.write_text(json.dumps(prior), encoding="utf-8")
        with (
            patch.object(m, "CACHE", str(cache_file)),
            patch.object(m, "PROJECTS", str(tmp_path / "projects")),
            patch.object(m, "latest_activity_mtime", return_value=now_s - 99999),
        ):
            m.main()
        data = json.loads(cache_file.read_text())
        assert data["active"] is True
        assert data["reset5h_ms"] == prior_reset

    def test_idle_expired_reset_writes_inactive(self, tmp_path):
        m = _load_module()
        now_s = time.time()
        now_ms = int(now_s * 1000)
        prior = {"source": "api", "reset5h_ms": now_ms - 1000, "util5h": 0.9, "fetched_ms": now_ms - 900_000}
        cache_file = tmp_path / "session-usage.json"
        cache_file.write_text(json.dumps(prior), encoding="utf-8")
        with (
            patch.object(m, "CACHE", str(cache_file)),
            patch.object(m, "PROJECTS", str(tmp_path / "projects")),
            patch.object(m, "latest_activity_mtime", return_value=now_s - 99999),
        ):
            m.main()
        data = json.loads(cache_file.read_text())
        assert data["active"] is False
