"""Versioned snapshots and encrypted transport. Never executes payload code."""
import contextlib
import datetime
import getpass
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from .common import MigrationError, save, load, sha, readonly, safe_child, backup_db, require_space, connect
from .files import snapshot_tree
from .history import STATE, HISTORY, version

SIDEBAR_KEYS = {"local-projects", "project-order", "pinned-project-ids", "pinned-thread-ids",
                "sidebar-project-thread-orders", "thread-project-assignments",
                "thread-project-membership-host-ids", "app-server-project-id-by-legacy-project-id-by-host"}


def inspect_profile(profile):
    profile = Path(profile)
    result = {"profile_exists": profile.is_dir(), "projects": [], "threads": 0, "state_version": None}
    if not (profile / STATE).is_file():
        return result
    result["state_version"] = version(profile / STATE)
    with readonly(profile / STATE) as d:
        result["threads"] = d.execute("select count(*) from threads").fetchone()[0]
        try:
            for p in d.execute("select id,name,position from projects order by position"):
                roots = [r[0] for r in d.execute("select path from project_roots where project_id=? order by position", (p["id"],))]
                result["projects"].append({"id": p["id"], "name": p["name"], "roots": roots, "position": p["position"]})
        except sqlite3.Error:
            result["issues"] = ["无法读取此版本的项目清单"]
    return result


def make_snapshot(profile, projects, destination, source_home=None, session_id=None):
    """Caller has already waited for shutdown; encryption happens before transport."""
    profile, destination = Path(profile).resolve(), Path(destination).resolve()
    source_home = Path(source_home or Path.home()).resolve()
    if destination.exists():
        raise MigrationError("快照目录已存在，使用继续入口而不覆盖")
    destination.mkdir(parents=True, mode=0o700)
    manifest = {"format_version": 1, "session_id": session_id or str(uuid.uuid4()),
                "source_home": str(source_home), "source_profile": str(profile),
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "projects": [], "rollouts": {}, "issues": [], "exclusions": [],
                "source_state_version": version(profile / STATE)}
    for name in (STATE, HISTORY):
        if not (profile / name).is_file():
            if name == STATE:
                raise MigrationError("未找到本地 Codex 历史库")
            manifest["issues"].append({"reason": "missing_history_database"})
            continue
        backup_db(profile / name, destination / "codex" / name)
        with connect(destination / "codex" / name) as d:
            allowed = ({"_sqlx_migrations", "threads", "projects", "project_roots", "thread_sections", "thread_spawn_edges"}
                       if name == STATE else {"_sqlx_migrations", "thread_turns", "thread_items", "thread_history_projection_state", "thread_realtime_items"})
            for (table,) in d.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'").fetchall():
                if manifest["source_state_version"] in {58, 59, 62} and table not in allowed:
                    d.execute('delete from "' + table.replace('"', '""') + '"')
            d.commit()
    sidebar = load(profile / ".codex-global-state.json", {})
    layout = {k: v for k, v in sidebar.items() if k in SIDEBAR_KEYS}
    prefs = sidebar.get("electron-persisted-atom-state", {}).get("flat-project-sidebar-preferences-v1", {})
    layout["sorting-preferences-v1"] = {k: v for k, v in prefs.items() if k in {"chatSortMode", "projectSortMode"} and v in {"manual", "updated_at"}}
    save(destination / "inventory/sidebar.json", layout)
    with readonly(profile / STATE) as d:
        fields = {r[1] for r in d.execute("pragma table_info(threads)")}
        threads = list(d.execute("select id,rollout_path from threads")) if {"id", "rollout_path"} <= fields else []
        if not {"id", "rollout_path"} <= fields:
            manifest["issues"].append({"reason": "unknown_thread_inventory_raw_database_preserved"})
        manifest["thread_count"] = len(threads)
    for t in threads:
        origin = Path(t["rollout_path"])
        if not origin.is_file() and Path(str(origin) + ".zst").is_file():
            origin = Path(str(origin) + ".zst")
        if not origin.is_file() or origin.is_symlink() or not origin.resolve().is_relative_to(source_home):
            manifest["issues"].append({"thread_id": t["id"], "reason": "missing_or_external_rollout"})
            continue
        key = hashlib.sha256(t["id"].encode()).hexdigest()
        rel = "rollouts/" + key + (".jsonl.zst" if origin.suffix == ".zst" else ".jsonl")
        target = destination / rel
        target.parent.mkdir(exist_ok=True)
        before = sha(origin)
        shutil.copy2(origin, target)
        if before != sha(target) or before != sha(origin):
            raise MigrationError("会话文件在备份期间改变，请完全退出后重试")
        manifest["rollouts"][t["id"]] = rel
    for i, item in enumerate(projects):
        root = Path(item["path"]).expanduser().resolve()
        if root in {source_home, profile} or profile.is_relative_to(root) or root.is_relative_to(profile) or root == Path("/"):
            raise MigrationError("请选择具体项目目录，不要选择整个用户目录或 Codex 数据目录")
        data = snapshot_tree(root, destination / "projects" / str(i))
        manifest["projects"].append({"key": str(i), "old_path": str(root), "name": item.get("name", root.name),
                                     "old_project_id": item.get("project_id"), "snapshot": "projects/" + str(i)})
        manifest["issues"].extend({"project": str(i), **x} for x in data["issues"])
        manifest["exclusions"].extend({"project": str(i), "path": x} for x in data["exclusions"])
    # Personal skills and text guidance only; system skills come from the new host.
    roots = [(profile / "skills", "environment/skills"), (source_home / ".agents/skills", "environment/agent-skills"),
             (profile / "memories", "environment/memories"), (profile / "attachments", "attachments")]
    for origin, rel in roots:
        if origin.is_dir():
            data = snapshot_tree(origin, destination / rel)
            manifest["issues"].extend({"area": rel, **x} for x in data["issues"])
    # Never restore cached system implementations as personal skills.
    for rel in ("environment/skills/.system", "environment/agent-skills/.system"):
        p = destination / rel
        if p.exists():
            shutil.rmtree(p)
    if (profile / "AGENTS.md").is_file():
        z = destination / "environment/AGENTS.md"
        z.parent.mkdir(exist_ok=True)
        shutil.copy2(profile / "AGENTS.md", z)
    manifest["files"] = [{"path": str(p.relative_to(destination)), "size": p.stat().st_size, "sha256": sha(p)}
                         for p in sorted(destination.rglob("*")) if p.is_file()]
    save(destination / "manifest.json", manifest)
    verify_snapshot(destination)
    return manifest


def verify_snapshot(snapshot):
    snapshot = Path(snapshot).resolve()
    manifest = load(snapshot / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
        raise MigrationError("迁移包格式不受支持")
    try:
        uuid.UUID(manifest["session_id"])
    except (KeyError, ValueError, TypeError):
        raise MigrationError("迁移包标识无效")
    for field in ("source_home", "source_profile"):
        value = Path(manifest.get(field, ""))
        if not value.is_absolute() or ".." in value.parts:
            raise MigrationError("迁移包的来源目录格式无效")
    for project in manifest.get("projects", []):
        key = project.get("key")
        if not isinstance(key, str) or not key.isdecimal() or project.get("snapshot") != "projects/" + key:
            raise MigrationError("迁移包的项目路径标识无效")
        old = Path(project.get("old_path", ""))
        if not old.is_absolute() or ".." in old.parts:
            raise MigrationError("迁移包的旧项目目录无效")
    expected = set()
    for entry in manifest.get("files", []):
        if entry["path"] in expected or entry["path"] == "manifest.json":
            raise MigrationError("迁移清单包含重复路径")
        p = safe_child(snapshot, entry["path"])
        if not p.is_file() or p.stat().st_size != entry["size"] or sha(p) != entry["sha256"]:
            raise MigrationError("文件尚未完整下载或校验不一致，请完成同步后继续")
        expected.add(entry["path"])
    actual = {str(p.relative_to(snapshot)) for p in snapshot.rglob("*") if p.is_file() and p != snapshot / "manifest.json"}
    if any(p.is_symlink() for p in snapshot.rglob("*")) or actual != expected:
        raise MigrationError("迁移包包含未声明文件或链接")
    for name in (STATE, HISTORY):
        p = snapshot / "codex" / name
        if p.is_file():
            with readonly(p) as d:
                if d.execute("pragma integrity_check").fetchone()[0] != "ok":
                    raise MigrationError("恢复源数据库完整性检查失败")
    return manifest


def password(new=False):
    value = getpass.getpass("设置迁移包密码：" if new else "请输入迁移包密码：")
    if not value or "\n" in value or "\0" in value:
        raise MigrationError("密码不能为空，且不能包含换行")
    if new and value != getpass.getpass("再次输入密码："):
        raise MigrationError("两次密码不同，未创建加密包")
    return value


def seal(snapshot, bundle, secret, runtime):
    bundle, runtime = Path(bundle), Path(runtime)
    bundle.mkdir(parents=True, exist_ok=True)
    if not (runtime / "codex-migration").is_file():
        raise MigrationError("未找到自带运行环境的恢复助手，请先构建发行包")
    manifest = verify_snapshot(snapshot)
    image = bundle / "迁移数据.dmg"
    temp = bundle / "迁移数据.preparing.dmg"
    temp.unlink(missing_ok=True)
    require_space(bundle, sum(x["size"] for x in manifest["files"]) + 128 * 1024 * 1024)
    r = subprocess.run(["/usr/bin/hdiutil", "create", "-srcfolder", str(snapshot), "-volname", "CodexMigration",
                        "-format", "UDZO", "-encryption", "AES-256", "-stdinpass", str(temp)],
                       input=(secret + "\0").encode(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if r.returncode:
        raise MigrationError("加密封包未完成；原始快照保留，可继续重试")
    os.replace(temp, image)
    helper = bundle / "恢复助手"
    if helper.exists():
        shutil.rmtree(helper)  # Generated tool code only, never user data.
    shutil.copytree(runtime, helper, symlinks=False)
    checks = []
    for p in sorted(helper.rglob("*")):
        if p.is_file():
            relative = str(p.relative_to(bundle))
            if any(c in relative for c in ("\n", "\r", "\\")):
                raise MigrationError("助手发行文件名不适合校验格式")
            checks.append(sha(p) + "  " + relative)
    (bundle / "助手校验.sha256").write_text("\n".join(checks) + "\n")
    launcher = bundle / "开始恢复.command"
    launcher.write_text('#!/bin/zsh\nset -eu\ntask_bundle_dir=${0:A:h}\ncd "$task_bundle_dir"\nprint "正在核验恢复助手；云盘可能正在下载，请稍候。"\n'
                        f'if [[ ! -f "助手校验.sha256" ]] || [[ $(/usr/bin/wc -l < "助手校验.sha256") -ne {len(checks)} ]] || ! /usr/bin/shasum -a 256 -c "助手校验.sha256" >/dev/null 2>&1; then\n'
                        '  print "恢复助手尚未完整下载或校验不一致。请等待同步完成，再双击此入口；没有开始恢复。"\n  read "task_wait?按回车关闭窗口："\n  exit 1\nfi\n'
                        '"$task_bundle_dir/恢复助手/codex-migration" restore --bundle "$task_bundle_dir" --interactive --wait\nread "task_done?按回车关闭窗口："\n')
    launcher.chmod(0o755)
    transport = {"format_version": 1, "session_id": manifest["session_id"],
                 "image": image.name, "size": image.stat().st_size, "sha256": sha(image)}
    # Read the encrypted artifact, not only the original staging directory.
    with mounted(bundle, secret, Path(snapshot).parent / "seal-verification", transport) as (_, checked):
        if checked["session_id"] != manifest["session_id"]:
            raise MigrationError("封包读回验证失败")
    save(bundle / "transport.json", transport)
    (bundle / "三步使用.txt").write_text("① 旧电脑：在 Codex 说‘我要换电脑’，按提示打包。\n② 新电脑：复制整个文件夹，双击‘开始恢复’，输入密码并退出客户端。\n③ 完成后打开 Codex，检查侧栏和一条旧聊天；有待处理项时按报告继续。\n不要清理旧电脑。此文件夹含个人私有数据，只交给自己的新电脑。\n")
    return load(bundle / "transport.json")


@contextlib.contextmanager
def mounted(bundle, secret, work, info=None):
    bundle, work = Path(bundle).resolve(), Path(work).resolve()
    info = info or load(bundle / "transport.json")
    if not info or info.get("format_version") != 1:
        raise MigrationError("没有完整的封包完成记录")
    image = safe_child(bundle, info["image"])
    if not image.is_file() or image.stat().st_size != info["size"] or sha(image) != info["sha256"]:
        raise MigrationError("迁移包尚未完整下载或已改变，请完成同步后继续")
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    point = Path(tempfile.mkdtemp(prefix="mount-", dir=work))
    r = subprocess.run(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(point),
                        "-stdinpass", str(image)], input=(secret + "\0").encode(),
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if r.returncode:
        point.rmdir()
        raise MigrationError("无法打开迁移包：请检查密码，数据未被恢复")
    try:
        manifest = verify_snapshot(point)
        if manifest["session_id"] != info["session_id"]:
            raise MigrationError("加密内容与外部清单不一致")
        yield point, manifest
    finally:
        subprocess.run(["/usr/bin/hdiutil", "detach", str(point)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if point.is_dir() and not os.path.ismount(point):
            point.rmdir()
