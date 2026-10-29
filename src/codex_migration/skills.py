"""Keep customized skill trees coherent; never mix two versions in one folder."""
import hashlib
import json
import shutil
from pathlib import Path
import yaml
from .common import MigrationError, sha


def signature(root):
    result = {str(p.relative_to(root)): sha(p) for p in Path(root).rglob("*") if p.is_file() and not p.is_symlink()}
    try:
        entry, meta, body = descriptor(root)
        result.pop(entry.name, None)
        result["SKILL.md"] = hashlib.sha256((json.dumps(meta, sort_keys=True, ensure_ascii=False) + body).encode()).hexdigest()
    except (MigrationError, yaml.YAMLError, UnicodeError):
        pass
    return result


def descriptor(root):
    for name in ("SKILL.md", "skill.md"):
        if (Path(root) / name).is_file():
            p = Path(root) / name
            raw = p.read_text()
            parts = raw.split("---", 2)
            if len(parts) != 3 or parts[0].strip():
                raise MigrationError("Skill 缺少可解析的元数据")
            data = yaml.safe_load(parts[1])
            if not isinstance(data, dict) or not isinstance(data.get("name"), str):
                raise MigrationError("Skill 缺少名称")
            return p, data, parts[2]
    raise MigrationError("Skill 缺少入口文件")


def skill_operations(origin, target, staging, session_id, reserved=None):
    origin, target, staging = map(Path, (origin, target, staging))
    operations, issues, expected = [], [], []
    reserved = {} if reserved is None else reserved
    for source in sorted(origin.iterdir()):
        if not source.is_dir() or source.name.startswith("."):
            continue
        try:
            entry, meta, body = descriptor(source)
        except (MigrationError, yaml.YAMLError, UnicodeError):
            issues.append({"reason": "invalid_skill_kept_as_reference", "skill": source.name})
            candidate = staging / "unparsed" / source.name
            if not candidate.exists():
                candidate.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, candidate)
            continue
        dest = target / source.name
        comparison = reserved.get(str(dest), dest)
        same = Path(comparison).is_dir() and signature(comparison) == signature(source)
        names = {}
        if target.is_dir():
            for p in target.rglob("SKILL.md"):
                # Skip bundled runtime copies of the migration tool's own assets.
                if "_runtime" in p.relative_to(target).parts:
                    continue
                try:
                    _, data, _ = descriptor(p.parent)
                    names.setdefault(data["name"], []).append(p.parent)
                except (MigrationError, yaml.YAMLError, UnicodeError):
                    pass
        other_name_collision = any(p != dest for p in names.get(meta["name"], []))
        for planned, staged in reserved.items():
            if Path(planned) != dest:
                try:
                    other_name_collision |= descriptor(staged)[1]["name"] == meta["name"]
                except (MigrationError, yaml.YAMLError, UnicodeError):
                    pass
        variant = ((dest.exists() or str(dest) in reserved) and not same) or other_name_collision
        source_to_use = source
        if variant or entry.name != "SKILL.md":
            suffix = hashlib.sha256((source.name + meta["name"] + json.dumps(signature(source), sort_keys=True)).encode()).hexdigest()[:6]
            folder = (source.name + "-migrated-" + session_id[:8] + "-" + suffix) if variant else source.name
            source_to_use = staging / folder
            if not source_to_use.exists():
                shutil.copytree(source, source_to_use)
                if entry.name != "SKILL.md":
                    (source_to_use / entry.name).unlink()
                if variant:
                    meta = dict(meta, name=folder)
                (source_to_use / "SKILL.md").write_text("---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip() + "\n---" + body)
            if variant:
                dest = target / folder
                issues.append({"reason": "custom_skill_version_kept_separately", "skill": source.name, "restored_as": folder})
        operations.append((source_to_use, dest, source.name))
        reserved[str(dest)] = source_to_use
        expected.append(str((dest / "SKILL.md").resolve()))
    return operations, issues, expected
