# 给 Agent 的使用指南

## 先辨别用户要做什么

- 换电脑、恢复本地 Codex：使用本工具。
- ChatGPT 云端聊天：先检查账号、工作区和历史可见性；本工具不能跨账号导入，也不能靠复制本地目录恢复云端历史。
- 开发或审计本工具：读根目录 AGENTS.md 和 CONTRIBUTING.md。

## 安装与最短调用路径

1. 确认用户使用 macOS Apple Silicon，且本机已有 Codex 客户端。读取 README 的当前边界。
2. 从 [Releases](https://github.com/Justin-2012/codex-migration/releases/tag/v0.1.0) 下载 macOS arm64 **发行包**及 SHA256SUMS。核对下载文件哈希，解压后再核对包内工具清单。不要运行私有备份里来源不明的程序，不要把 GitHub 源码压缩包当成运行环境。
3. 帮用户运行发行包中的 `安装迁移Skill.command`；安装器会校验文件并保留已有改造版。若系统阻止未公证程序，说明当前预览版的限制并停止，不关闭 Gatekeeper。
4. 读取实际安装目录中的 `SKILL.md`；默认在 `~/.codex/skills/codex-migration`，已配置 CODEX_HOME 时遵循原配置。直接使用该目录下 `_runtime/codex-migration`，不需要 Python 或 Homebrew。

安装后最短调用：

```sh
"<installed-skill>/_runtime/codex-migration" doctor
"<installed-skill>/_runtime/codex-migration" launch pack
```

`<installed-skill>` 是占位符，必须替换成已核实的真实路径。不要让用户复制命令手动执行。`launch pack` 会让用户选择项目和输出位置，并打开独立窗口；该窗口就绪后，用户才可以按提示退出客户端。密码只在本地窗口输入。

已有迁移包时：

```sh
"<installed-skill>/_runtime/codex-migration" launch restore --bundle "<bundle-folder>"
```

新机没有安装 Skill 时，用户也可以直接双击迁移包自带的 `开始恢复.command`。恢复助手一次确认范围和自定义离线导入授权，等待客户端退出，然后处理。不要抢先关闭当前聊天，也不要自行改库。

## 状态、续跑和验收

- 用 `status` 读取已存在的会话；恢复中断重新打开原迁移包，封包中断使用原 session 的 `launch pack --resume <id>`。不要删状态、重建会话来绕过中断。
- 用 `verify --bundle <folder>` 核验源包完整性，不写目标数据。
- 默认状态目录：`~/Library/Application Support/CodexMigration/sessions`。以实际配置为准，读取会话 `state.json`、`result.json` 和 `验收结果.html`。
- 报告机器验证结果后，请用户打开 Codex 检查侧栏及一条旧聊天。只有收到确认，才记录 `verify --session <id> --confirm-visual`。它记录用户确认，不会抹掉已知缺项。
- 回滚先读 `status` 和 Skill 的 references/operations.md；新数据已发生变化时保留现状和备份，不强行覆盖。

## 不可省略的边界

不要默认传递 `--authorize-history`。不要复制旧登录和审批配置，不启用自动化或外部账号，不自动清理旧机。未知版本只做支持的文件恢复与参考导出；原生恢复、文件恢复和 Skill 发现是不同验收项。不要声称所有第三方 Skill 的运行依赖已修复。

公开反馈只保留版本号和脱敏错误摘要。真实聊天、附件、数据库、保护备份和验收报告都属于用户私有资料。
