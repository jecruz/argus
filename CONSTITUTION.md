# Argus Constitution

**Project:** Argus — CAISS Yield Desktop Widget
**Redmine:** [#1035](http://10.0.0.23:8085/issues/1035) (CAISS Yield project #89)
**Bundle ID:** `com.caiss.argus`
**Installed runtime:** `~/Applications/Argus.app`

---

## Purpose

Argus is the desktop observability surface for CAISS Yield. It shows live LLM
provider usage, quota state, and reset windows for every agent running on this
machine. It is read-only — it never routes, budgets, or issues commands.

---

## Architecture Rules

1. **Two layers only.** Pollers (Python, launchd) write JSON snapshots. The
   Swift widget reads them. No direct network calls from Swift.
2. **Pollers are stdlib-only.** No third-party Python dependencies in poller
   scripts. Use only the standard library.
3. **Cache files are private.** All JSON snapshots written with mode `0o600`
   via `tempfile.mkstemp` + `os.fchmod`. Never world-readable.
4. **Logs stay private.** launchd log paths live under `~/.claude/logs/`, not
   `/tmp/`.
5. **No hardcoded secrets.** OAuth tokens come from the macOS Keychain at
   runtime. API keys must never appear in source or cache files.
6. **Cache location is `~/.claude/` today.** When CAISS Yield ships its
   service runtime, caches migrate to `~/.caiss/yield/`. Do not skip this.

---

## Security Baseline

All changes must preserve these properties, established in the initial security
hardening pass:

- Swift binary built with `--options runtime` (Hardened Runtime, blocks DYLD
  injection).
- `write_cache()` uses `tempfile.mkstemp` with `os.fchmod(fd, 0o600)`.
- launchd `StandardOutPath` / `StandardErrorPath` point to `~/.claude/logs/`.
- `install.sh` resolves `python3` via `/usr/bin/python3` first, not `$PATH`.
- `codesign` failures are surfaced, not swallowed with `|| true`.

---

## Testing

- **Python pollers:** 100% line coverage required. Run:
  ```bash
  python3 -m pytest tests/ -v --cov=session_usage_poll --cov=codex_usage_poll --cov-report=term-missing
  ```
- **Swift widget:** No automated tests today (no Xcode project). Logic changes
  to `Sessions.compute()` or `UsageCache.load()` must be manually verified
  with a fresh `./build.sh && open Argus.app`.
- Tests must pass before every commit that touches Python poller code.

---

## Commit Rules

- Follow Conventional Commits: `type(scope): description`
- Valid types: `feat`, `fix`, `refactor`, `test`, `chore`, `docs`, `security`
- Scope: `argus`, `poller`, `swift`, `install`, `tests`
- Max **400 lines changed per commit**. Split larger changes into logical units.
- Link Redmine issues: `Redmine: #NNN` in commit body.
- No `.env` files, no secrets, no binaries > 5 MB.

---

## Branch Strategy

| Pattern                | Purpose                         |
| ---------------------- | ------------------------------- |
| `main`                 | Stable, always installable      |
| `feat/NNN-description` | New features (branch from main) |
| `fix/NNN-description`  | Bug fixes                       |
| `chore/description`    | Maintenance                     |

- Rebase onto `main` (no merge commits).
- Delete branches after merge.

---

## Adding a New Provider

1. Write a new poller (`{provider}_usage_poll.py`), stdlib-only.
2. Output JSON to `~/.claude/{provider}-usage.json` (mode `0o600`).
3. Add launchd plist to `install.sh` and cleanup to `uninstall.sh`.
4. Add a Swift `UsageCache`-style loader and a new card or row in the widget.
5. Write pytest tests achieving 100% coverage of the new poller.
6. Create a Redmine ticket under CAISS Yield (#89) before starting.
