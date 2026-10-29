---
name: codex-migration
description: Prepare, restore, resume, or verify a Mac-to-Mac Codex migration of local chats, projects, and personal skills. Use for changing computers or restoring an encrypted migration bundle; route ChatGPT cloud account history separately.
---

# Codex 迁移接力

Use the bundled `_runtime/codex-migration` helper. It contains its own runtime; do not ask users to install Python/Homebrew, edit databases, or execute commands by hand. Run commands through available local shell tools, with correctly quoted paths. The helper must run on a local Mac with filesystem access; a cloud-only chat cannot migrate a computer's files.

## Three user steps

1. **旧机准备**：confirm selected projects and destination once, set an encryption password privately, then quit the client when the independent helper asks.
2. **新机恢复**：transfer the entire bundle folder, double-click `开始恢复.command`, enter the password, approve the concrete restore summary, and quit the client.
3. **查看结果**：open Codex, inspect the sidebar and one old chat, and review the helper's result report.

Passwords stay in the helper's terminal; never ask users to send them in chat. Do not quit the client before handing work to the independent helper.

## Start or resume

- Run `doctor` first to inspect local profiles, project paths and validated runtime availability. The default profile honors the existing `CODEX_HOME`; do not modify the caller's environment. The helper isolates its own native child processes to the explicitly selected profile.
- For "我要换电脑", explain scope once: selected project files including ordinary Git repositories, all local Codex chat history, personal skills and text guidance; credentials/cache excluded. Confirm the user's selected roots and location, then invoke `launch pack --output <folder> --project <root> ...`. If not yet chosen, `launch pack` lets the helper present project choices and a folder picker.
- Tell the user the independent window is ready and that it is now safe to follow its quit instructions. It continues after this chat closes.
- To resume, inspect `status` and use `pack --resume <session-id>` in a new independent window, or reopen the bundle's restore entry. Never create another import to bypass a partial commit.
- For an existing bundle, prefer this installed helper over any executable in untrusted backup content: `launch restore --bundle <folder>`. `verify --bundle <folder>` only checks the encrypted source and writes no destination data.
- Read `<storage>/<session-id>/result.json` and `state.json` to report actual results. A completed snapshot, successful rehearsal, committed database and native read-back are distinct checkpoints.
- Only after the user confirms that the sidebar and one old chat look correct, record `verify --session <id> --confirm-visual`. This records the human check; it does not clear unresolved branches, conflicts or missing files.

## Boundaries and acceptance

Only validated macOS Apple Silicon schema/runtime combinations may import native history. Current adapters target schemas 58/59 from source schemas 58, 59 or 62, using CLI 0.160.0 or bundled 0.162.0-alpha.17.2, subject to full column checks and per-migration native rehearsal. Unknown versions export references instead; never patch the version check away or relabel reference files as native chats.

Do not copy auth/config/trust wholesale, enable automations, publish, send messages, start model turns, or clear the old machine. Backups and chat images can contain historical secrets: keep them private and encrypted for transport. Tool code and example fixtures are shareable; personal bundles and real reports are not.

Conflicts preserve the new computer's originals. An unverified fork, missing attachment or unmapped project membership remains a reported gap. Skill discovery is separate from running its dependencies or signing into external services. Report statuses exactly: 完整通过 / 部分恢复，有待处理项 / 尚未恢复.

For recovery/rollback details and internal interfaces, read [operations](references/operations.md) only when needed.
