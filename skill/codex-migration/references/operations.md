# Operations

`doctor` inventories only. `pack` creates a private snapshot and encrypted DMG. `restore` validates, backs up, restores files, rehearses offline history in copies, commits the pair of SQLite databases atomically, restores sidebar mappings, and reads history back through the local app-server. `verify` reads the encrypted bundle only. `status` reads durable progress. `rollback --session ID` requires the client closed and refuses database rollback after new destination changes.

All commands accept `--profile PATH` and `--storage PATH`; defaults are the existing Codex profile and `~/Library/Application Support/CodexMigration/sessions`. Paths must be quoted. `restore --map OLD=NEW` is an explicit project-path override; defaults replace the source home prefix and put external roots under `~/Codex迁移项目`.

Use `launch pack` / `launch restore --bundle PATH` to create and open one independent Terminal window. It accepts `--output PATH` and repeated `--project PATH`. Do not run a blocking wait inside the active Codex chat and then ask the user to quit it: the independent window must exist first.

A failed restore reuses its bundle UUID. Atomic receipt rows distinguish a committed history transaction from an interrupted JSON report write. Files have exclusive-create publication and durable ownership receipts. Reopen the same entry to continue; never delete receipts to retry. For a pack interruption, use its `pack-state.json` and `pack --resume ID`; do not refresh source files into the already recorded snapshot.

Offline merge needs explicit authorization for the concrete plan. The interactive helper asks once per run before mutation. Noninteractive `--authorize-history` is for an agent carrying an existing explicit authorization, not a default flag to add automatically.

If new chats, edited configuration or layout changes make whole-database rollback unsafe, preserve both current state and the backup and explain the stop. Do not replace the whole `.codex` directory. The full local protection backup may contain current-machine login files; it must never be shared or transported as the public tool.

ChatGPT cloud chats/projects use account/workspace access checks, not this Codex database adapter. Conversation sync does not transfer a local project folder. Cross-account, Windows and Intel native imports are not supported in v0.1.
