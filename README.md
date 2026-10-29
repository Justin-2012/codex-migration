# Codex 迁移接力 · Codex Migration

把本地聊天、项目文件、侧栏和自定义 Skill 带到新 Mac。**安装一次，之后告诉 Agent「我要换电脑」。**

[下载安装包](https://github.com/Justin-2012/codex-migration/releases/tag/v0.1.0) · [让 Agent 开始](docs/AGENT_GUIDE.md) · [English](docs/README.en.md) · [验证记录](docs/validation-v0.1.0.json)

> **v0.1.0 预览版 / Experimental preview**：个人维护，非 OpenAI 官方导入工具。当前限定 macOS Apple Silicon 和经过验证的应用组合。已完成同机独立配置迁移验证；尚未完成另一台实体 Mac 验收、Developer ID 签名和 Apple 公证。未知版本不会执行原生历史合并。请先阅读边界，再决定试用。

个人维护的 macOS Apple Silicon 本地迁移工具。Skill 接受一句话指令，独立助手在退出客户端后继续；不需要用户安装 Python 或 Homebrew。

## 安装一次

从 [Releases](https://github.com/Justin-2012/codex-migration/releases/tag/v0.1.0) 下载 `codex-migration-v0.1.0-macos-arm64.zip`，完整解压到本机，再双击“安装迁移Skill.command”。之后在 Codex 中说：**使用 $codex-migration，我要换电脑。** 新机只需已有 Codex；迁移包自带恢复助手。

下载页面同时提供 `SHA256SUMS`，可交给 Agent 校验压缩包；安装入口还会检查解压后的工具文件完整性。若 macOS 提示无法验证开发者，先停止安装并检查来源；当前预览版不要求关闭 Gatekeeper 或移除隔离标记。GitHub 自动生成的 Source code 压缩包是源码，不能代替自带运行环境的安装包。

## 交给你的 Agent

复制这段话给拥有本机文件和终端权限的 Agent：

> 请阅读 https://github.com/Justin-2012/codex-migration 的 README.md 和 docs/AGENT_GUIDE.md，协助我安装发行版，并启动旧机准备或新机恢复。先检查系统和兼容范围；密码由我在本机输入。不要自行修改数据库或关闭客户端。

Agent 开发本项目时先读 [AGENTS.md](AGENTS.md)。只有 Skill 文本不足以迁移，必须同时安装发行版中的自带运行环境助手。云端 Agent 无法直接迁移本机磁盘。

## 三步完成

1. **旧机准备**：确认要带走的项目和保存位置，设置密码；助手提示后按 Cmd+Q 退出客户端。
2. **新机恢复**：把整个迁移包文件夹带到新机，双击“开始恢复”，输入密码，确认恢复范围并退出客户端。
3. **查看结果**：助手处理完后打开 Codex，检查侧栏和一条旧聊天，查看验收报告。旧机不会被清理。

传输可用 iCloud、其他云盘或移动硬盘。目录可见不代表已下载；新机完整读取、哈希一致才通过。对选中项目打包业务文件和普通 Git 资料，凭证与依赖缓存排除，外部链接及 Git 工作树明确报出。新机同名冲突保留双方。

## 能力与边界

- 包含聊天与分页历史、可读会话文件、项目、个人 Skill、文本指导及附件目录。登录态、原始账号配置、旧审批和自动化不启用。
- 当前原生适配器是源 schema 58/59/62 → 目标 58/59，可使用已验证的 CLI 0.160.0 或应用自带的 0.162.0-alpha.17.2。每次迁移重新检查表结构、演练回滚，并通过原生接口核验消息内容与顺序；不因版本号相同就直接导入。
- 未知组合只恢复文件、导出聊天参考资料，并标记“部分恢复”。分支来源链、缺附件、未映射项目和 Skill 冲突不冒充成功。不会为兼容而修改库版本号。
- ChatGPT 云端历史与工作区单独检查；不提供跨账号合并。Windows、Intel、持续双向同步和线上插件发布不在首版支持范围。
- 验收报告为私有资料。公开分享发行包和源代码，**不要分享自己的加密数据包、保护备份、真实聊天或报告**。历史文字/图片可能含旧密钥，排除凭证文件不能保证聊天中没有敏感内容。
- 未进行外部账号、全部 Skill 依赖或项目构建验收。结果会区分完整通过、部分恢复和尚未恢复。

## 中断与回滚

恢复中断后重新打开同一个“开始恢复”；不会重复新增已提交线程。旧机封包中断，在 Codex 中让 Skill 读取状态并继续。不要删除状态文件重新导入。

需要回滚时告诉 Skill“回滚这次迁移”。数据库或布局已有新变化时，助手会停止覆盖并保留现有数据；回滚文件时也保留用户后来修改的内容。备份保存在本机私有 `~/Library/Application Support/CodexMigration/sessions`。

## 开发与验证

源码使用 Python 3.11+，依赖声明见 pyproject.toml。安装项目依赖后执行 `PYTHONPATH=src python -m unittest discover -s tests -v`。端到端合成数据验证使用 `PYTHONPATH=src python tools/integration.py`；它不使用真实聊天，不修改活动配置目录。

在 macOS arm64 的隔离构建环境安装 PyInstaller，运行 `python tools/build.py`。产物位于 dist。内部命令为 doctor、pack、restore、verify、status、rollback；用户通过 Skill 或双击入口调用。

构建细节、测试命令和贡献约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。持续集成只验证合成单元测试，不代表完成原生客户端兼容验收。已验证的应用构建为 `26.1007.30912 (20156)`；机器运行 `doctor` 的结果才决定本次能否原生导入。支持范围随适配器变化，见 [兼容清单](src/codex_migration/assets/compatibility.json)。

本地构建使用临时签名，不代表已通过 Apple 公证或跨机器验收。签名、公证与独立机器测试是正式稳定版前的待办。

## 项目结构与反馈

| 入口 | 内容 |
| --- | --- |
| `src/codex_migration/` | 加密快照、文件恢复、原生适配、续跑和回滚引擎 |
| `skill/codex-migration/` | Agent Skill 指令与操作约定 |
| `tools/build.py` | 生成自带 Python 运行环境的发行包 |
| `tools/integration.py` | 隔离配置中的原生迁移验收 |
| `tests/` | 完全合成的回归样例 |

请在 [Issues](https://github.com/Justin-2012/codex-migration/issues) 提交系统、应用版本和脱敏错误。**不要上传迁移数据包、数据库、会话文件、私有报告或密码。** 安全问题先看 [SECURITY.md](SECURITY.md)。

源代码按 [MIT License](LICENSE) 提供，运行环境的依赖许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
