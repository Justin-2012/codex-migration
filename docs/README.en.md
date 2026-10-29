# Codex Migration

A community-maintained, local Mac-to-Mac migration helper for Codex chats, project files, sidebar layout and personal skills. Not an official OpenAI import tool.

**Experimental v0.1.0:** macOS Apple Silicon only. Native adapters are limited to tested combinations; unknown versions export history references instead. Tests used independent profiles on one Mac. A second physical Mac, Developer ID signing and Apple notarization have not been verified/completed.

## Three steps

1. On the old Mac, download the [arm64 release](https://github.com/Justin-2012/codex-migration/releases/tag/v0.1.0), extract it and open `安装迁移Skill.command` to install the skill. Tell Codex: `Use $codex-migration; I am moving to a new computer.` Select projects and output location, set a password privately, then quit the client when the independent helper asks.
2. Transfer the **entire generated migration folder** to the new Mac. Double-click `开始恢复.command`, enter your password, approve the restore summary and quit the client as prompted. The new Mac needs Codex installed, but not Python/Homebrew or a separate tool installation.
3. Open Codex, check the sidebar and an old chat, then review the result report. Unresolved items remain explicit. The old Mac is not cleaned up.

The release includes its runtime. GitHub's automatically generated source archives are for developers. Check SHA256SUMS before installation. This preview is not notarized; do not disable Gatekeeper to install it.

Encrypted immutable snapshots can travel through cloud drives or removable storage. Full local reads and file hashes establish arrival; do not live-sync active chat databases. Conflicting destination files and customized skills are preserved.

ChatGPT cloud account history, cross-account imports, Windows, Intel and continuous sync are outside this release. Credentials and old permissions are not restored. Existing local protection backups can contain sensitive data: never publish them or real reports.

Agents: read [AGENT_GUIDE.md](AGENT_GUIDE.md) and [the skill](../skill/codex-migration/SKILL.md). Developers: read [AGENTS.md](../AGENTS.md) and [CONTRIBUTING.md](../CONTRIBUTING.md). The helper's current user interface is Chinese. See the [Chinese README](../README.md) for compatibility and detailed behavior.
