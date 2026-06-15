# Argus — CAISS Yield desktop widget

A tiny floating macOS desktop widget that shows live LLM provider usage.
Part of **CAISS Yield** (Redmine #89) — Argus is the desktop observability
surface for your local agents.

Currently supports two providers:

- **Claude** — your 5-hour usage window, live "resets in" countdown, and
  % used, matching what claude.ai shows (driven by Anthropic's rate-limit
  headers).
- **Codex** — your Codex tokens and sessions over the last 24 hours, with
  the most-used model.

The widget has two stacked cards: top = Claude, bottom = Codex.

## Requirements

- macOS (Apple Silicon or Intel)
- **Xcode command line tools** (`xcode-select --install`) — for `swiftc`
- A Claude **Pro/Max** subscription used via **Claude Code** (for the
  Claude card; the Codex card works with no network at all)

## Install

```bash
cd argus
./install.sh
```

That builds the widget from source, installs it to `~/Applications/Argus.app`,
and sets up three per-user launchd agents that start at login. **No sudo,
nothing system-wide.**

Prefer zero network calls? Install the widget only — it will estimate the
Claude window from your local transcripts, and the Codex card will show
nothing if the poller isn't running:

```bash
./install.sh --widget-only
```

Uninstall any time:

```bash
./uninstall.sh
```

## What the agents do

| agent                          | interval | network | purpose                               |
| ------------------------------ | -------- | ------- | ------------------------------------- |
| `com.caiss.argus`              | always   | no      | the floating widget                   |
| `com.caiss.argus.poller`       | 10 min   | yes     | read Claude rate-limit headers → JSON |
| `com.caiss.argus.codex-poller` | 10 min   | no      | read `~/.codex/state_5.sqlite` → JSON |

The Claude poller makes one tiny request every 10 minutes to
`api.anthropic.com` using the OAuth token Claude Code already saved in
your Mac keychain. Its only purpose is to read the rate-limit headers
Anthropic attaches to every reply — the call itself is essentially free
and bills to your subscription. (macOS may show a one-time keychain
"allow" prompt the first time.)

The Codex poller reads from a local SQLite file — no network at all.

## Files

| file                          | what it is                                             |
| ----------------------------- | ------------------------------------------------------ |
| `SessionWidget.swift`         | the widget (native AppKit, no dependencies)            |
| `session_usage_poll.py`       | the 10-min Claude poller (Python stdlib only)          |
| `codex_usage_poll.py`         | the 10-min Codex poller (Python stdlib only)           |
| `build.sh`                    | compiles `Argus.app` with `swiftc`                     |
| `install.sh` / `uninstall.sh` | per-user launchd setup / teardown                      |
| `tests/`                      | pytest, 100% line coverage on both pollers             |
| `CONSTITUTION.md`             | architecture + commit + testing rules for contributors |

Logs: `~/.claude/logs/argus.log`, `~/.claude/logs/argus-poller.log`,
`~/.claude/logs/argus-codex-poller.log`.
Cache: `~/.claude/session-usage.json` and `~/.claude/codex-usage.json`
(inspect them any time — both are mode `0o600`).

## Development

Tests:

```bash
python3 -m pytest tests/ -v --cov=session_usage_poll --cov=codex_usage_poll
```

Build:

```bash
bash build.sh && open Argus.app
```

See `CONSTITUTION.md` for the architecture rules and commit conventions
this project follows. See `CLAUDE.md` for project-local agent guidance.

## Notes / FAQ

- **Will the Claude token expire?** Claude Code refreshes it in the keychain
  as you use it. If it's ever stale the poller just keeps the last known
  reset and the widget falls back to the transcript estimate.
- **Codex tokens are not directly comparable to Claude tokens** — different
  pricing, different models. The Codex card is a relative signal, not a
  budget figure.
- **Where does this fit in CAISS?** Argus is the read-only desktop
  observability surface for CAISS Yield (the routing/budgeting service).
  The service writes its own caches to `~/.caiss/yield/`; until that ships,
  Argus reads from `~/.claude/`. See Redmine #89 and the parent ticket #1035.

MIT licensed.
