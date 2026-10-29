"""Copy selected files; a durable intent journal makes interrupted additions safe."""
import os
import shutil
import stat
import uuid
from pathlib import Path
from .common import MigrationError, load, save, sha, safe_child, no_symlink_parents, fsync_dir

CACHES = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".next", ".cache", ".DS_Store"}
SECRETS = {"auth.json", "credentials.json", ".ssh", ".aws", ".gnupg", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519"}


def excluded(name):
    return (name in CACHES or name.lower() in SECRETS or
            (name.startswith(".env") and name not in {".env.example", ".env.sample", ".env.template"}) or
            Path(name).suffix.lower() in {".pem", ".key", ".p12", ".pfx"})


def snapshot_tree(source, destination):
    source, destination = Path(source).resolve(), Path(destination)
    result = {"files": [], "issues": [], "exclusions": []}
    if not source.is_dir():
        raise MigrationError("所选项目目录不存在或尚未下载")
    if destination.resolve().is_relative_to(source):
        raise MigrationError("不能把迁移快照存进所选项目内部")
    if (source / ".git").is_file():
        raise MigrationError("项目使用外部 Git 工作树，请先单独整理；未将其标记为完整备份")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    for folder, dirs, files in os.walk(source, followlinks=False):
        base = Path(folder)
        for name in list(dirs):
            p = base / name
            rel = str(p.relative_to(source))
            if excluded(name):
                result["exclusions"].append(rel)
                dirs.remove(name)
            elif p.is_symlink():
                result["issues"].append({"path": rel, "reason": "symlink_not_followed"})
                dirs.remove(name)
        for name in files:
            p = base / name
            rel = str(p.relative_to(source))
            if excluded(name):
                result["exclusions"].append(rel)
                continue
            if p.is_symlink() or not p.is_file():
                result["issues"].append({"path": rel, "reason": "link_or_special_file_not_copied"})
                continue
            z = destination / rel
            z.parent.mkdir(parents=True, exist_ok=True)
            before = sha(p)
            shutil.copy2(p, z)
            if before != sha(z) or before != sha(p):
                raise MigrationError("备份时源文件发生变化，请保存项目后重试")
            result["files"].append({"path": rel, "size": z.stat().st_size, "sha256": before})
    return result


def restore_tree(source, destination, receipt, conflicts, exclude=()):
    source, destination, receipt, conflicts = map(Path, (source, destination, receipt, conflicts))
    if destination.is_symlink():
        raise MigrationError("恢复目标不能是符号链接")
    no_symlink_parents(destination)
    destination.mkdir(parents=True, exist_ok=True)
    state = load(receipt, {"created": [], "conflicts": [], "intents": []})
    # A hardlink to the staged inode proves ownership even if power was lost
    # between publication of the file and publication of the receipt.
    for intent in state["intents"]:
        target = safe_child(destination, intent["path"])
        temp = Path(intent["temp"])
        no_symlink_parents(temp)
        if temp.parent != target.parent or temp.is_symlink():
            raise MigrationError("无效的续跑文件记录")
        if temp.exists() and not target.exists():
            os.link(temp, target)
        if temp.exists() and target.exists() and os.path.samefile(temp, target):
            entry = {"path": intent["path"], "sha256": intent["sha256"]}
            if entry not in state["created"]:
                state["created"].append(entry)
            save(receipt, state)
        temp.unlink(missing_ok=True)
    state["intents"] = []
    save(receipt, state)
    for p in sorted(source.rglob("*")):
        if p.is_symlink():
            raise MigrationError("迁移快照中出现未声明的符号链接")
        if not p.is_file():
            continue
        rel = str(p.relative_to(source))
        if rel in exclude:
            continue
        z = safe_child(destination, rel)
        h = sha(p)
        if z.exists():
            if not z.is_file() or sha(z) != h:
                conflict = safe_child(conflicts, rel)
                conflict.parent.mkdir(parents=True, exist_ok=True)
                if conflict.exists() and (not conflict.is_file() or sha(conflict) != h):
                    raise MigrationError("冲突候选文件已被修改，停止覆盖")
                if not conflict.exists():
                    shutil.copy2(p, conflict)
                entry = {"path": rel, "candidate": str(conflict)}
                if entry not in state["conflicts"]:
                    state["conflicts"].append(entry)
            save(receipt, state)
            continue
        z.parent.mkdir(parents=True, exist_ok=True)
        temp = z.parent / (".codex-migration-" + uuid.uuid4().hex + ".tmp")
        with temp.open("xb") as out, p.open("rb") as inp:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, stat.S_IMODE(p.stat().st_mode) & 0o777)
        intent = {"path": rel, "sha256": h, "temp": str(temp)}
        state["intents"].append(intent)
        save(receipt, state)
        os.link(temp, z)  # O_EXCL semantics: never replaces an existing file.
        state["created"].append({"path": rel, "sha256": h})
        save(receipt, state)
        temp.unlink()
        state["intents"].remove(intent)
        save(receipt, state)
        fsync_dir(z.parent)
    return state


def undo_tree(destination, receipt):
    result = {"removed": [], "kept_modified": []}
    state = load(receipt, {"created": []})
    for row in reversed(state["created"]):
        p = safe_child(destination, row["path"])
        if not p.exists():
            continue
        if p.is_file() and sha(p) == row["sha256"]:
            p.unlink()
            result["removed"].append(row["path"])
        else:
            result["kept_modified"].append(row["path"])
    return result
