# Argus — CAISS Yield Desktop Widget

Floating macOS desktop widget that shows live LLM provider usage.
Part of CAISS Yield (Redmine #1035). Bundle ID: `com.caiss.argus`.

## Stack

- Python 3 (stdlib only) — `session_usage_poll.py` Claude poller
- Swift (AppKit, no dependencies) — `SessionWidget.swift` widget
- Tests: `pytest` via `requirements-dev.txt`

## Conventions

- Python pollers must stay stdlib-only; do not add third-party imports.
- Cache files always written with mode 0o600.
- Temp files must use `tempfile.mkstemp` in the same directory as the target.
- Cache location today: `~/.claude/session-usage.json`
  Moves to `~/.caiss/yield/` when CAISS Yield service ships.

## Testing

```bash
python3 -m pytest tests/ -v --tb=short
```

## Redmine

- #1035 — Argus: CAISS Yield desktop widget (parent)
- #1036 — Rename and rebrand (this task)
- #1037 — Add Codex provider panel
