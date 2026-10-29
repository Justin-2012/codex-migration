import json
from pathlib import Path
from .common import load, save, sha, safe_child, readonly


def projection_paths(snapshot, manifest, mapper, profile, session):
    """Resolve local image references only to checksum-matching copied bytes."""
    files = {x["path"]: x["sha256"] for x in manifest["files"]}
    paths, missing = {}, set()
    history = Path(snapshot) / "codex/thread_history_1.sqlite"
    if not history.is_file():
        return {}, []
    with readonly(history) as d:
        if not d.execute("select 1 from sqlite_master where name='thread_items'").fetchone():
            return {}, []
        if "item_json" not in {r[1] for r in d.execute("pragma table_info(thread_items)")}:
            return {}, [{"reason": "unknown_attachment_projection_structure"}]
        for (raw,) in d.execute("select item_json from thread_items"):
            item = json.loads(raw)
            if item.get("type") != "userMessage":
                continue
            for content in item.get("content", []):
                old = content.get("path")
                if not isinstance(old, str) or not Path(old).is_absolute():
                    continue
                candidates = []
                for p in manifest["projects"]:
                    if Path(old).is_relative_to(p["old_path"]):
                        relative = str(Path(old).relative_to(p["old_path"]))
                        key = p["snapshot"] + "/" + relative
                        candidates.extend([(Path(mapper(old)), key), (Path(session) / "conflicts" / ("project-" + p["key"]) / relative, key)])
                prefix = str(Path(manifest["source_profile"]) / "attachments")
                if Path(old).is_relative_to(prefix):
                    relative = str(Path(old).relative_to(prefix))
                    candidates.extend([(safe_child(Path(profile) / "attachments", relative), "attachments/" + relative),
                                       (safe_child(Path(session) / "conflicts/attachments", relative), "attachments/" + relative)])
                match = next((p for p, key in candidates if key in files and p.is_file() and not p.is_symlink() and sha(p) == files[key]), None)
                if match:
                    paths[old] = str(match)
                else:
                    missing.add(old)
    issues = [{"reason": "local_attachment_not_resolved", "count": len(missing)}] if missing else []
    return paths, issues


def merge_text_index(snapshot, manifest, profile):
    origin = Path(snapshot) / "attachments/pasted-text-attachments.json"
    if not origin.is_file():
        return None
    index = Path(profile) / "attachments/pasted-text-attachments.json"
    before = load(index, {"attachmentPaths": [], "pendingRemovalPaths": [], "textExcerptsByPath": {}})
    out = dict(before)
    paths, excerpts = list(before.get("attachmentPaths", [])), dict(before.get("textExcerptsByPath", {}))
    old = load(origin)
    prefix = str(Path(manifest["source_profile"]) / "attachments") + "/"
    def remap(value):
        if not value.startswith(prefix):
            return None
        relative = value[len(prefix):]
        original = safe_child(Path(snapshot) / "attachments", relative)
        dest = safe_child(Path(profile) / "attachments", relative)
        return str(dest) if original.is_file() and dest.is_file() and sha(original) == sha(dest) else None
    for p in old.get("attachmentPaths", []):
        new = remap(p)
        if new and new not in paths:
            paths.append(new)
    for p, excerpt in old.get("textExcerptsByPath", {}).items():
        new = remap(p)
        if new:
            excerpts.setdefault(new, excerpt)
    out.update(attachmentPaths=paths, textExcerptsByPath=excerpts)
    save(index, out)
    return {"after_sha256": sha(index), "old_pending_removals_not_imported": True}
