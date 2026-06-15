# Claude Session Widget

Floating macOS desktop widget that displays Claude Code 5-hour usage window.

## Stack

- Python 3 (stdlib only) — `session_usage_poll.py` poller
- Swift (AppKit, no dependencies) — `SessionWidget.swift` widget
- Tests: `pytest` via `requirements-dev.txt`

## Conventions

- `session_usage_poll.py` must stay stdlib-only; do not add third-party imports.
- Cache file lives at `~/.claude/session-usage.json`; always write with mode 0o600.
- Temp files must use `tempfile.mkstemp` in the same directory as the target.

## Testing

```bash
python3 -m pytest tests/ -v --tb=short
```
