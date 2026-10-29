# Agent instructions

This repository contains a local macOS migration tool, not migration data. Start with README.md. For using the tool on a computer, read docs/AGENT_GUIDE.md and skill/codex-migration/SKILL.md. For code changes, follow CONTRIBUTING.md.

## Data boundaries

- Never commit real chats, databases, encrypted personal bundles, credentials, user reports or protection backups. All tests use synthetic data. Keep outputs in ignored build/ and dist/.
- Do not read or modify a live Codex profile as a development test. Use isolated temporary profiles and pass both native child CODEX_HOME and sqlite_home; sqlite_home alone is insufficient isolation.
- Do not run model turns or enable external MCP services during migration validation. Do not change the caller's HOME or CODEX_HOME.
- Do not quit or kill a client on the user's behalf. Hand off to an independent helper before asking the user to quit normally.
- Offline native history import requires the user's explicit authorization, a complete target backup and successful rehearsal. Do not add --authorize-history by default.

## Implementation rules

Preserve destination files, chats, sidebar state and customized skills. Keep source immutable. Unknown application/schema/runtime combinations must fall back to reference export; never weaken compatibility checks or claim native success. Preserve transaction receipts and durable file ownership journals across retries. Do not overwrite current data to force a rollback after new user changes.

Changes to adapters require actual native read-back of message content/order on isolated profiles and rollback evidence. Unit tests alone do not establish support for a new app build. Keep experimental support and unverified physical-device behavior explicit.

## Checks

Install development dependencies in a venv, then run:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

Run native integration only on a compatible macOS arm64 host with the documented native runtime; see CONTRIBUTING.md. Review staged files for personal information before publishing. Public validation records must contain synthetic results only.
