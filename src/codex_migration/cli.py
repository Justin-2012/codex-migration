import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from . import __version__
from .common import MigrationError, save, load, lock, sha
from .platform import default_profile, default_storage, check_platform, wait_closed, app_info, reopen_app
from .bundle import inspect_profile, make_snapshot, verify_snapshot, password, seal, mounted
from .engine import restore_snapshot, rollback_session, write_report, confirm_visual, Mapper
from .rpc import find_cli


def runtime_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    raise MigrationError("此入口需要构建后的自带运行环境助手；开发测试请直接调用 Python API")


def skill_templates():
    if getattr(sys, "frozen", False):
        import importlib.resources
        return json.loads(importlib.resources.files("codex_migration").joinpath("assets/skill-template.json").read_text())
    source = Path(__file__).resolve().parents[2] / "skill/codex-migration"
    return {str(p.relative_to(source)): p.read_text() for p in source.rglob("*") if p.is_file()}


def install(profile):
    profile = Path(profile).resolve()
    templates = skill_templates()
    target = profile / "skills/codex-migration"
    if target.is_symlink():
        raise MigrationError("现有 Skill 是链接，未覆盖")
    if target.exists():
        if not (target / "SKILL.md").is_file() or sha(target / "SKILL.md") != hashlib.sha256(templates["SKILL.md"].encode()).hexdigest():
            raise MigrationError("已存在其他版本或改造版迁移 Skill，保留原件；请先检查版本冲突")
        installed = load(target / "installation.json")
        if not installed:
            raise MigrationError("现有安装缺少完整性凭据，请保留原目录后重新安装")
        from .common import safe_child
        for entry in installed["files"]:
            p = safe_child(target, entry["path"])
            if not p.is_file() or sha(p) != entry["sha256"]:
                raise MigrationError("现有安装不完整或已改造，未覆盖")
        return {"installed": True, "already_installed": True, "skill": str(target), "version": __version__}
    runtime = runtime_dir()
    stage = profile / "skills" / (".migration-install-" + uuid.uuid4().hex)
    stage.parent.mkdir(parents=True, exist_ok=True)
    try:
        from .common import safe_child
        stage.mkdir()
        for rel, text in templates.items():
            p = safe_child(stage, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        # Trusted release code only: materialize internal framework aliases so
        # subsequent data snapshots do not rely on preserving executable links.
        for p in runtime.rglob("*"):
            if p.is_symlink() and not p.resolve().is_relative_to(runtime.resolve()):
                raise MigrationError("发行助手包含外部运行时链接")
        shutil.copytree(runtime, stage / "_runtime", symlinks=False)
        files = [{"path": str(p.relative_to(stage)), "sha256": sha(p)} for p in sorted(stage.rglob("*")) if p.is_file()]
        save(stage / "installation.json", {"version": __version__, "files": files})
        if target.exists():
            raise MigrationError("安装期间目标目录出现，请检查现有版本")
        os.rename(stage, target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {"installed": True, "skill": str(target), "version": __version__}


def choose_projects(info):
    candidates = []
    print("请选择要带走文件的项目；聊天和个人 Skill 会一并备份。", flush=True)
    for p in info["projects"]:
        roots = [r for r in p["roots"] if Path(r).is_dir() and not "/.chatgpt-projects/" in r]
        if not roots:
            continue
        candidates.append((p, roots))
        print(f'{len(candidates)}. {p["name"]}（{len(roots)} 个可读目录）', flush=True)
    if not candidates:
        print("未找到可选项目目录；仅打包聊天与个人 Skill。", flush=True)
        return []
    answer = input("回车选择以上全部；或输入项目序号，用逗号分隔；输入 0 只带聊天和 Skill：").strip()
    if answer == "0":
        return []
    try:
        indices = list(range(len(candidates))) if not answer else list(dict.fromkeys(int(x.strip()) - 1 for x in answer.split(",")))
        if any(i < 0 or i >= len(candidates) for i in indices):
            raise ValueError()
    except ValueError:
        raise MigrationError("项目序号无效，尚未开始打包")
    result = []
    for i in indices:
        p, roots = candidates[i]
        for root in roots:
            result.append({"path": root, "name": p["name"], "project_id": p["id"]})
    return result


def choose_folder():
    r = subprocess.run(["/usr/bin/osascript", "-e", 'POSIX path of (choose folder with prompt "选择迁移包保存位置：云盘或移动硬盘目录")'],
                       capture_output=True, text=True)
    if r.returncode:
        raise MigrationError("已取消选择保存位置")
    return Path(r.stdout.strip())


def launch(args):
    runtime = runtime_dir()
    folder = Path(args.storage) / ("launch-" + uuid.uuid4().hex)
    folder.mkdir(parents=True, mode=0o700)
    command = [str(runtime / "codex-migration"), args.action, "--profile", str(args.profile), "--storage", str(args.storage), "--interactive", "--wait"]
    if args.output:
        command.extend(["--output", str(args.output)])
    for p in args.project:
        command.extend(["--project", p])
    if args.bundle:
        command.extend(["--bundle", str(args.bundle)])
    if args.action == "pack" and args.resume:
        command.extend(["--resume", args.resume])
    script = folder / "迁移接力.command"
    script.write_text("#!/bin/zsh\n" + shlex.join(command) + '\nread "task_done?按回车关闭窗口："\n')
    script.chmod(0o700)
    subprocess.run(["/usr/bin/open", "-a", "Terminal", str(script)], check=True)
    return {"launched": True, "script": str(script), "message": "助手已在独立窗口接棒，请按窗口提示操作。"}


def run_pack(args):
    check_platform()
    info = inspect_profile(args.profile)
    if not info["profile_exists"] or info["state_version"] is None:
        raise MigrationError("未找到可备份的 Codex 历史")
    prior = load(Path(args.storage) / ("pack-" + args.resume) / "pack-state.json") if args.resume else None
    output = Path(prior["output"] if prior else (args.output or (choose_folder() if args.interactive else "")))
    if not args.output and not args.interactive and not prior:
        raise MigrationError("请指定保存位置")
    projects = [{"path": p, "name": Path(p).name} for p in args.project]
    if args.interactive and not args.project and not args.resume:
        projects = choose_projects(info)
    session_id = args.resume or str(uuid.uuid4())
    uuid.UUID(session_id)
    stage = Path(args.storage) / ("pack-" + session_id)
    stage.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = stage / "pack-state.json"
    state = load(state_path)
    if state:
        if Path(state["profile"]) != Path(args.profile).resolve():
            raise MigrationError("续跑记录不属于此配置目录")
        output, projects = Path(state["output"]), state["projects"]
    else:
        state = {"session_id": session_id, "profile": str(Path(args.profile).resolve()), "output": str(output.resolve()),
                 "projects": projects, "phase": "waiting_for_exit"}
        save(state_path, state)
    secret = password(new=True)
    wait_closed(args.wait)
    with lock(Path(args.storage) / "migration.lock"):
        snapshot = stage / "snapshot"
        if not (snapshot / "manifest.json").is_file():
            if snapshot.exists():
                shutil.rmtree(snapshot)
            print("正在生成旧机快照…", flush=True)
            make_snapshot(args.profile, projects, snapshot, session_id=session_id)
        else:
            verify_snapshot(snapshot)
        bundle = output / ("Codex迁移包-" + session_id[:8])
        print("正在加密并核验迁移包…", flush=True)
        transport = seal(snapshot, bundle, secret, runtime_dir())
        state.update(phase="packed", bundle=str(bundle), transport=transport)
        save(state_path, state)
        print("旧机准备完成。请把整个迁移包文件夹带到新机，双击‘开始恢复’。", flush=True)
        print("旧电脑文件没有删除。迁移包位置：" + str(bundle), flush=True)
        return {"phase": "packed", "bundle": str(bundle), "session_id": session_id}


def run_restore(args):
    check_platform()
    if not args.bundle:
        raise MigrationError("请打开迁移包中的‘开始恢复’")
    secret = password()
    with mounted(args.bundle, secret, Path(args.storage) / "mounts") as (snapshot, manifest):
        mappings = {}
        for item in args.map:
            old, separator, new = item.partition("=")
            if not separator or not Path(old).is_absolute() or not Path(new).is_absolute():
                raise MigrationError("路径映射应为绝对旧路径=绝对新路径")
            mappings[old] = new
        binding = {"profile": str(Path(args.profile).resolve()), "manifest_sha256": sha(snapshot / "manifest.json"),
                   "mapping": Mapper(manifest, Path.home(), mappings).roots, "custom_history_merge": True}
        authorization = Path(args.storage) / manifest["session_id"] / "authorization.json"
        authorized = args.authorize_history or load(authorization) == binding
        if args.interactive and not authorized:
            print(f'将恢复 {len(manifest["projects"])} 组项目文件、{manifest.get("thread_count", 0)} 条历史线程（含内部子线程）及个人 Skill。', flush=True)
            for old, new in binding["mapping"].items():
                print("项目文件位置：" + new, flush=True)
            print("先备份新机，再增量恢复并保留已有数据；包含经过副本演练的自定义离线聊天合并。", flush=True)
            print("不恢复旧登录或审批，不清理旧机。", flush=True)
            authorized = input("同意此次恢复请输入‘恢复’；其他输入取消：").strip() == "恢复"
            if not authorized:
                raise MigrationError("已取消恢复，没有修改新机数据")
        if authorized:
            save(authorization, binding)
        state = restore_snapshot(snapshot, args.profile, args.storage, overrides=mappings,
                                 authorize_history=authorized, wait=args.wait)
    report = Path(args.storage) / manifest["session_id"] / "验收结果.html"
    print("迁移处理完成。请打开 Codex，检查侧栏和一条旧聊天。", flush=True)
    print("验收结果：" + str(report), flush=True)
    if args.interactive:
        subprocess.run(["/usr/bin/open", str(report)], check=False)
        reopen_app()
        if input("侧栏和一条旧聊天正常后，回到此窗口输入‘确认’；直接回车保留待确认：").strip() == "确认":
            final = confirm_visual(Path(args.storage) / manifest["session_id"])
            print("验收记录：" + final["status"], flush=True)
    return {"phase": state["phase"], "report": str(report), "session_id": state["session_id"]}


def parser():
    p = argparse.ArgumentParser(prog="codex-migration", description="三步完成 Codex 电脑迁移")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("doctor", "pack", "restore", "verify", "status", "rollback", "install", "launch"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--profile", type=Path, default=default_profile())
        cmd.add_argument("--storage", type=Path, default=default_storage() / "sessions")
        if name in {"pack", "restore", "rollback", "launch"}:
            cmd.add_argument("--wait", action="store_true")
            cmd.add_argument("--interactive", action="store_true")
        if name in {"pack", "launch"}:
            cmd.add_argument("--output", type=Path)
            cmd.add_argument("--project", action="append", default=[])
        if name == "pack":
            cmd.add_argument("--resume")
        if name in {"restore", "verify", "launch"}:
            cmd.add_argument("--bundle", type=Path)
        if name == "restore":
            cmd.add_argument("--authorize-history", action="store_true")
            cmd.add_argument("--map", action="append", default=[])
        if name == "verify":
            cmd.add_argument("--session")
            cmd.add_argument("--confirm-visual", action="store_true")
        if name in {"status", "rollback"}:
            cmd.add_argument("--session", required=name == "rollback")
        if name == "launch":
            cmd.add_argument("action", choices=["pack", "restore"])
            cmd.add_argument("--resume")
    return p


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    try:
        for key in ("session", "resume"):
            value = getattr(args, key, None)
            if value:
                uuid.UUID(value)
        if args.command == "doctor":
            info = inspect_profile(args.profile)
            cli, v = find_cli(info["state_version"])
            result = {"tool_version": __version__, "platform": platform.system(), "architecture": platform.machine(),
                      "app": app_info(), "native_cli": cli, "native_cli_version": v, **info}
        elif args.command == "pack":
            result = run_pack(args)
        elif args.command == "restore":
            result = run_restore(args)
        elif args.command == "verify":
            if args.session:
                result = confirm_visual(Path(args.storage) / args.session) if args.confirm_visual else load(Path(args.storage) / args.session / "result.json")
            elif not args.bundle:
                raise MigrationError("请指定迁移包")
            else:
                if args.confirm_visual:
                    raise MigrationError("用户验收确认需要对应的迁移记录")
                with mounted(args.bundle, password(), Path(args.storage) / "mounts") as (_, manifest):
                    result = {"verified": True, "session_id": manifest["session_id"], "files": len(manifest["files"]), "restored": False}
        elif args.command == "status":
            if args.session:
                result = load(Path(args.storage) / args.session / "state.json") or load(Path(args.storage) / ("pack-" + args.session) / "pack-state.json")
            else:
                result = [{"session": p.parent.name, "phase": load(p).get("phase")} for name in ("state.json", "pack-state.json") for p in Path(args.storage).glob("*/" + name)]
        elif args.command == "rollback":
            if args.interactive and input("回滚仅撤销本工具的迁移；有新变化时会停止。输入‘回滚’确认：").strip() != "回滚":
                raise MigrationError("已取消回滚")
            result = rollback_session(Path(args.storage) / args.session, args.wait)
        elif args.command == "install":
            check_platform()
            result = install(args.profile)
        else:
            result = launch(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        print("已暂停操作，原始资料保留。重新打开相同入口即可继续。", file=sys.stderr)
        return 130
    except (MigrationError, OSError, ValueError, sqlite3.Error) as e:
        print("迁移停止：" + str(e), file=sys.stderr)
        return 1
