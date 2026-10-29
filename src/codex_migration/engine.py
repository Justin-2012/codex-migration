from .common import connect
import copy
import hashlib
import html
import json
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from .common import MigrationError, save, load, sha, readonly, backup_db, lock, require_space, safe_child, no_symlink_parents
from .files import restore_tree, undo_tree
from .history import (STATE, HISTORY, version, compatible, prepare_rollouts, apply_history, fingerprint,
                      connect_pair, rollback_databases, receipt)
from .bundle import verify_snapshot
from .rpc import RPC, find_cli
from .platform import wait_closed, app_info
from .skills import skill_operations
from .attachments import projection_paths, merge_text_index


class Mapper:
    def __init__(self, manifest, home, overrides=None):
        self.old_home, self.home = Path(manifest["source_home"]), Path(home).resolve()
        self.roots = dict(overrides or {})
        for p in manifest["projects"]:
            old = Path(p["old_path"])
            if str(old) in self.roots:
                continue
            if old.is_relative_to(self.old_home):
                new = self.home / old.relative_to(self.old_home)
            else:
                new = self.home / "Codex迁移项目" / (p["key"] + "-" + old.name)
            self.roots[str(old)] = str(new)

    def __call__(self, value):
        path = Path(value)
        for old, new in sorted(self.roots.items(), key=lambda x: len(x[0]), reverse=True):
            if path.is_relative_to(old):
                return str(Path(new) / path.relative_to(old))
        if path.is_relative_to(self.old_home):
            return str(self.home / path.relative_to(self.old_home))
        return str(self.home / "Codex迁移接续")


def backup_profile(profile, backup):
    profile, backup = Path(profile), Path(backup)
    if backup.exists():
        manifest = load(backup / "backup.json")
        if not manifest:
            raise MigrationError("上次备份未完成，请检查后重新准备备份目录")
        for entry in manifest["files"]:
            p = safe_child(backup / "profile", entry["path"])
            if not p.is_file() or sha(p) != entry["sha256"]:
                raise MigrationError("保护备份校验失败，停止恢复")
        return manifest
    backup.mkdir(parents=True, mode=0o700)
    def transient(folder, names):
        return [n for n in names if n.endswith(("-wal", "-shm")) or (Path(folder) / n).is_socket() or (Path(folder) / n).is_fifo()]
    shutil.copytree(profile, backup / "profile", symlinks=True, ignore=transient)
    for n in (STATE, HISTORY):
        if (profile / n).is_file():
            backup_db(profile / n, backup / n)
    files = []
    links = []
    for p in sorted((backup / "profile").rglob("*")):
        rel = str(p.relative_to(backup / "profile"))
        if p.is_symlink():
            if os.readlink(p) != os.readlink(profile / rel):
                raise MigrationError("备份链接核验失败")
            links.append({"path": rel, "target": os.readlink(p)})
        elif p.is_file():
            h = sha(p)
            if sha(profile / rel) != h:
                raise MigrationError("本机配置在备份期间变化，请完全退出后继续")
            files.append({"path": rel, "sha256": h})
    result = {"files": files, "links": links}
    save(backup / "backup.json", result)
    return result


def project_plan(snapshot, staged, manifest, mapper, cli, logs, config_profile):
    sidebar = load(Path(snapshot) / "inventory/sidebar.json", {})
    old_entries = []
    selected = {p["old_path"] for p in manifest["projects"]}
    with readonly(Path(snapshot) / "codex" / STATE) as d:
        for p in d.execute("select * from projects order by position"):
            roots = [r[0] for r in d.execute("select path from project_roots where project_id=? order by position", (p["id"],)) if r[0] in selected]
            if roots:
                old_entries.append({"old_id": p["id"], "old_ids": [p["id"]], "name": p["name"], "old_roots": roots})
    # Some versions store canonical IDs in SQLite and memberships under legacy UI IDs.
    for alias, p in sidebar.get("local-projects", {}).items():
        roots = [r for r in p.get("rootPaths", []) if r in selected]
        if not roots:
            continue
        found = next((x for x in old_entries if set(x["old_roots"]) == set(roots)), None)
        if found:
            found["old_ids"].append(alias)
        else:
            old_entries.append({"old_id": alias, "old_ids": [alias], "name": p["name"], "old_roots": roots})
    # Explicitly selected folders without an existing project are also restored as projects.
    known_roots = {p for e in old_entries for p in e["old_roots"]}
    for p in manifest["projects"]:
        if p["old_path"] not in known_roots:
            key = "folder-" + p["key"]
            old_entries.append({"old_id": key, "old_ids": [key], "name": p["name"], "old_roots": [p["old_path"]]})
    rpc = RPC(cli, staged, logs, config_profile)
    try:
        existing = rpc.call("project/list", {})["data"]
        for entry in old_entries:
            roots = [mapper(r) for r in entry["old_roots"]]
            match = next((p for p in existing if sorted(r["path"] for r in p["roots"]) == sorted(roots)), None)
            if not match:
                result = rpc.call("project/create", {"idempotencyKey": "migration-" + manifest["session_id"] + "-" + entry["old_id"],
                                                      "name": entry["name"], "roots": [{"path": r} for r in roots]})
                match = result["project"]
                existing.append(match)
            entry["id"], entry["roots"] = match["id"], roots
    finally:
        rpc.close()
    with readonly(Path(staged) / STATE) as d:
        for entry in old_entries:
            row = dict(d.execute("select * from projects where id=?", (entry["id"],)).fetchone())
            entry["name"] = row["name"]
            entry.update({k: row[k] for k in ("position", "created_at_ms", "updated_at_ms")})
    mapping = {old: e["id"] for e in old_entries for old in e["old_ids"]}
    assignments = {tid: mapping[v["projectId"]] for tid, v in sidebar.get("thread-project-assignments", {}).items()
                   if v.get("projectKind") == "local" and v.get("projectId") in mapping}
    return old_entries, assignments, mapping


def merge_sidebar(profile, source, projects, assignments, mapping, selected):
    path = Path(profile) / ".codex-global-state.json"
    before = load(path, {})
    out = copy.deepcopy(before)
    # Resolve native IDs to current UI aliases by roots, preserving existing names.
    local = out.setdefault("local-projects", {})
    host = "local:" + str(Path(profile).resolve())
    idmaps = out.setdefault("app-server-project-id-by-legacy-project-id-by-host", {}).setdefault(host, {})
    native_to_ui = {v: k for k, v in idmaps.items()}
    for p in projects:
        alias = next((key for key, value in local.items() if sorted(value.get("rootPaths", [])) == sorted(p["roots"])), native_to_ui.get(p["id"], p["id"]))
        if alias not in local:
            local[alias] = {"id": alias, "name": p["name"], "rootPaths": p["roots"], "createdAt": p["created_at_ms"], "updatedAt": p["updated_at_ms"]}
        idmaps[alias] = p["id"]
        native_to_ui[p["id"]] = alias
    desired = [native_to_ui[mapping[x]] for x in source.get("project-order", []) if x in mapping]
    desired.extend(native_to_ui[p["id"]] for p in projects)
    out["project-order"] = list(dict.fromkeys(desired + out.get("project-order", []) + list(local)))
    old_pins = [native_to_ui[mapping[x]] for x in source.get("pinned-project-ids", []) if x in mapping]
    out["pinned-project-ids"] = list(dict.fromkeys(old_pins + out.get("pinned-project-ids", [])))
    out["pinned-thread-ids"] = list(dict.fromkeys(out.get("pinned-thread-ids", []) + [x for x in source.get("pinned-thread-ids", []) if x in selected]))
    members = out.setdefault("thread-project-assignments", {})
    hosts = out.setdefault("thread-project-membership-host-ids", {})
    for tid, pid in assignments.items():
        if tid in selected:
            members.setdefault(tid, {"projectKind": "local", "projectId": native_to_ui[pid]})
            hosts.setdefault(tid, "local")
    orders = out.setdefault("sidebar-project-thread-orders", {})
    for alias, value in source.get("sidebar-project-thread-orders", {}).items():
        if alias not in mapping:
            continue
        key = native_to_ui[mapping[alias]]
        current = orders.setdefault(key, {"threadIds": []})
        incoming = [tid for tid in value.get("threadIds", []) if tid in selected and assignments.get(tid) == mapping[alias]]
        current["threadIds"] = list(dict.fromkeys(incoming + current.get("threadIds", [])))
    for key in ("local-projects", "thread-project-assignments", "thread-project-membership-host-ids"):
        for k, v in before.get(key, {}).items():
            if out[key][k] != v:
                raise MigrationError("本机已有侧栏记录发生变化，停止写入")
    preferences = {k: v for k, v in source.get("sorting-preferences-v1", {}).items() if k in {"chatSortMode", "projectSortMode"} and v in {"manual", "updated_at"}}
    if preferences:
        out.setdefault("electron-persisted-atom-state", {}).setdefault("flat-project-sidebar-preferences-v1", {}).update(preferences)
    save(path, out)
    return {"projects": len(projects), "assigned_threads": sum(t in selected for t in assignments), "existing_memberships_preserved": True}


def native_readback(snapshot, profile, prepared, cli, logs, config_profile, resume_samples=False, attachment_paths=None):
    result = {"threads": 0, "items": 0, "message_items_checked": 0, "resume_samples": [], "turn_started": False}
    rpc = RPC(cli, profile, logs, config_profile)
    try:
        with readonly(Path(snapshot) / "codex" / HISTORY) as source:
            for tid in prepared["included"]:
                expected = list(source.execute("select item_json from thread_items where thread_id=? order by rollout_ordinal,item_id", (tid,)))
                got, cursor = [], None
                while True:
                    params = {"threadId": tid, "limit": 100, "sortDirection": "asc"}
                    if cursor:
                        params["cursor"] = cursor
                    page = rpc.call("thread/items/list", params)
                    got.extend(x["item"] for x in page["data"])
                    cursor = page.get("nextCursor")
                    if not cursor:
                        break
                if len(got) != len(expected):
                    raise MigrationError("原生历史条目数量不一致")
                for old, new in zip(expected, got):
                    old = json.loads(old[0])
                    if old["id"] != new["id"] or old["type"] != new["type"]:
                        raise MigrationError("原生历史顺序或类型不一致")
                    if old["type"] == "agentMessage":
                        if old.get("text") != new.get("text"):
                            raise MigrationError("助手消息内容不一致")
                        result["message_items_checked"] += 1
                    elif old["type"] == "userMessage":
                        for content in old.get("content", []):
                            if attachment_paths and content.get("path") in attachment_paths:
                                content["path"] = attachment_paths[content["path"]]
                        fields = lambda v: [(x.get("type"), x.get("text"), x.get("path"), x.get("url")) for x in v.get("content", [])]
                        if fields(old) != fields(new):
                            raise MigrationError("用户消息或附件字段不一致")
                        result["message_items_checked"] += 1
                result["threads"] += 1
                result["items"] += len(got)
                if result["threads"] % 100 == 0:
                    print(f'已核验历史 {result["threads"]} 条线程', flush=True)
        if resume_samples and prepared["included"]:
            with readonly(Path(profile) / STATE) as d:
                rows = list(d.execute("select id,created_at,updated_at,archived from threads"))
            rows = [r for r in rows if r["id"] in prepared["included"]]
            sample_ids = list(dict.fromkeys([min(rows, key=lambda r: r["created_at"])["id"], max(rows, key=lambda r: r["updated_at"])["id"]] + [r["id"] for r in rows if r["archived"]][:1]))
            for tid in sample_ids:
                original = Path(prepared["included"][tid]["path"])
                sample = Path(logs) / "resume-samples" / original.name
                sample.parent.mkdir(exist_ok=True)
                shutil.copy2(original, sample)
                with connect(Path(profile) / STATE) as edit:
                    edit.execute("update threads set rollout_path=? where id=?", (str(sample), tid))
                resumed = rpc.call("thread/resume", {"threadId": tid, "cwd": str(Path(logs)), "runtimeWorkspaceRoots": [str(Path(logs))],
                                                    "approvalPolicy": "on-request", "sandbox": "read-only", "excludeTurns": True})
                if resumed["thread"]["id"] != tid:
                    raise MigrationError("续接样本未读回原线程")
                if resumed["thread"].get("status", {}).get("type") != "idle":
                    raise MigrationError("续接样本没有进入可继续状态")
                result["resume_samples"].append({"thread_id": tid, "status": resumed["thread"].get("status"), "turn_started": False})
    finally:
        rpc.close()
    for r in prepared["included"].values():
        if sha(r["path"]) != r["sha256"]:
            raise MigrationError("原生验证改变了安全会话原件")
    result["passed"] = True
    return result


def export_references(snapshot, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    history = Path(snapshot) / "codex" / HISTORY
    if not history.is_file():
        return {"history_exported": False}
    (destination / "raw").mkdir(exist_ok=True)
    for name in (STATE, HISTORY):
        p = Path(snapshot) / "codex" / name
        if p.is_file():
            shutil.copy2(p, destination / "raw" / name)
    with readonly(history) as d:
        fields = {r[1] for r in d.execute('pragma table_info(thread_items)')}
        if not {"thread_id", "item_json", "rollout_ordinal", "item_id"} <= fields:
            return {"history_exported": False, "raw_database_preserved": True, "reference_only": True}
        for (tid,) in d.execute("select distinct thread_id from thread_items"):
            key = hashlib.sha256(tid.encode()).hexdigest()
            with (destination / (key + ".jsonl")).open("w") as f:
                for (item,) in d.execute("select item_json from thread_items where thread_id=? order by rollout_ordinal,item_id", (tid,)):
                    f.write(item + "\n")
    return {"history_exported": True, "reference_only": True}


def restore_snapshot(snapshot, profile, storage, home=None, overrides=None, authorize_history=False, wait=False):
    snapshot, profile, storage = map(lambda p: Path(p).resolve(), (snapshot, profile, storage))
    manifest = verify_snapshot(snapshot)
    source_digest = sha(snapshot / "manifest.json")
    session = storage / manifest["session_id"]
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(storage / "migration.lock"):
        state_path = session / "state.json"
        state = load(state_path, {"session_id": manifest["session_id"], "phase": "not_restored", "profile": str(profile), "operations": [], "issues": list(manifest.get("issues", []))})
        if state["profile"] != str(profile):
            raise MigrationError("此迁移记录对应其他配置目录")
        if state.get("source_manifest_sha256", source_digest) != source_digest:
            raise MigrationError("同一迁移标识的源清单已改变，拒绝沿用旧授权或续跑")
        state["source_manifest_sha256"] = source_digest
        if state["phase"] in {"complete", "partial"}:
            write_report(session, state)
            return state
        if state["phase"] == "rolled_back":
            raise MigrationError("此迁移已回滚；重新迁移请生成新的迁移包")
        mapper = Mapper(manifest, home or Path.home(), overrides)
        if "path_mapping" in state and state["path_mapping"] != mapper.roots:
            raise MigrationError("续跑的路径映射发生变化，停止以保护已有文件")
        state["path_mapping"] = mapper.roots
        save(state_path, state)
        wait_closed(wait)
        if not profile.is_dir() or not (profile / STATE).is_file():
            raise MigrationError("请先安装并启动一次新机 Codex，再退出后继续")
        require_space(storage, sum(x["size"] for x in manifest["files"]) * 2 + 128 * 1024 * 1024)
        backup = session / "backup"
        print("正在保护新电脑现有数据…", flush=True)
        if not state.get("backup_done"):
            if backup.exists() and not (backup / "backup.json").is_file():
                # Only an uncommitted backup directory is disposable, never source/profile.
                shutil.rmtree(backup)
            backup_profile(profile, backup)
            state.update(backup_done=True, backup_root=str(backup), phase="backed_up")
            save(state_path, state)
        print("正在恢复项目文件与个人 Skill…", flush=True)
        operations = []
        for p in manifest["projects"]:
            operations.append((safe_child(snapshot, p["snapshot"]), Path(mapper(p["old_path"])), "project-" + p["key"]))
        expected_skills = []
        reserved_skills = {}
        for area in ("environment/skills", "environment/agent-skills"):
            origin = Path(snapshot) / area
            if origin.is_dir():
                skill_ops, issues, expected = skill_operations(origin, profile / "skills", session / "skill-variants" / area.replace("/", "-"), manifest["session_id"], reserved_skills)
                expected_skills.extend(expected)
                state["issues"].extend(x for x in issues if x not in state["issues"])
                for origin, target, key in skill_ops:
                    operations.append((origin, target, area.replace("/", "-") + "-" + key))
        for area, target in (("environment/memories", profile / "memories"), ("attachments", profile / "attachments")):
            origin = Path(snapshot) / area
            if origin.is_dir():
                operations.append((origin, target, area.replace("/", "-")))
        guidance = Path(snapshot) / "environment/AGENTS.md"
        if guidance.is_file():
            area = session / "guidance"
            area.mkdir(exist_ok=True)
            shutil.copy2(guidance, area / "AGENTS.md")
            operations.append((area, profile, "guidance"))
        for origin, target, key in operations:
            no_symlink_parents(target)
            target = target.resolve() if not target.is_symlink() else target
            protected = [profile, Path(home or Path.home()).resolve() / ".ssh", Path(home or Path.home()).resolve() / ".aws", Path(home or Path.home()).resolve() / ".gnupg", Path(home or Path.home()).resolve() / "Library/LaunchAgents"]
            if key.startswith("project-") and (target == Path(home or Path.home()).resolve() or any(target == p or target.is_relative_to(p) or p.is_relative_to(target) for p in protected)):
                raise MigrationError("项目映射覆盖了受保护的配置目录，已停止")
            ledger = session / "files" / (key + ".json")
            op = {"destination": str(target), "receipt": str(ledger)}
            if op not in state["operations"]:
                state["operations"].append(op)
                save(state_path, state)
            restored = restore_tree(origin, target, ledger, session / "conflicts" / key,
                                    exclude=("pasted-text-attachments.json",) if key == "attachments" else ())
            for entry in restored["conflicts"]:
                issue = {"reason": "existing_file_conflict_kept", "area": key, "path": entry["path"]}
                if issue not in state["issues"]:
                    state["issues"].append(issue)
        state["phase"] = "files_restored"
        state["expected_skill_paths"] = expected_skills
        state["attachment_index"] = merge_text_index(snapshot, manifest, profile)
        attachment_paths, attachment_issues = projection_paths(snapshot, manifest, mapper, profile, session)
        state["issues"].extend(x for x in attachment_issues if x not in state["issues"])
        state["attachment_paths_remapped"] = len(attachment_paths)
        save(state_path, state)
        cli, cli_version = find_cli(version(profile / STATE))
        import importlib.resources
        matrix = load(Path(str(importlib.resources.files("codex_migration").joinpath("assets/compatibility.json"))))
        app = app_info()
        if not app or {"version": app["version"], "build": app["build"]} not in matrix["app_versions"]:
            cli = None
            cli_version = "客户端版本未经本适配器验证；保留文件与参考资料，不做原生合并"
        if not (profile / HISTORY).exists() and cli and authorize_history and version(profile / STATE) in {58, 59}:
            from .bootstrap import empty_history
            print("正在初始化新机空历史库…", flush=True)
            template = empty_history(profile, session / "empty-history", cli)
            # Capture an empty baseline before exclusive publication to the profile.
            backup_db(template, backup / HISTORY)
            ready = session / "empty-history-ready.sqlite"
            backup_db(template, ready)
            wait_closed(False)
            temp = profile / (".migration-empty-history-" + manifest["session_id"])
            with ready.open("rb") as a, temp.open("wb") as z:
                shutil.copyfileobj(a, z)
                z.flush()
                os.fsync(z.fileno())
            os.link(temp, profile / HISTORY)
            temp.unlink()
            state["initialized_missing_history"] = True
            save(state_path, state)
        ok, reason = compatible(Path(snapshot) / "codex", profile)
        if not ok or not cli or not authorize_history:
            export_references(snapshot, session / "references")
            state["issues"].append({"reason": reason if not ok else ("history_authorization_missing" if not authorize_history else cli_version)})
            state["phase"] = "partial"
            state["native_history_restored"] = False
            save(state_path, state)
            write_report(session, state)
            return state
        prepared_path = session / "safe-rollouts/prepared.json"
        if prepared_path.is_file():
            prepared = load(prepared_path)
            for r in prepared["included"].values():
                if sha(r["path"]) != r["sha256"]:
                    raise MigrationError("安全会话副本改变，停止续跑")
        else:
            print("正在重置旧设备权限并准备历史…", flush=True)
            prepared = prepare_rollouts(snapshot, session / "safe-rollouts", manifest, mapper)
        if prepared["held"]:
            export_references(snapshot, session / "references")
        sidebar = load(Path(snapshot) / "inventory/sidebar.json", {})
        plan_path = session / "history-plan.json"
        if not plan_path.exists():
            rehearsal = session / "rehearsal"
            if rehearsal.exists():
                shutil.rmtree(rehearsal)
            rehearsal.mkdir()
            for n in (STATE, HISTORY):
                backup_db(profile / n, rehearsal / n)
            projects, assignments, mapping = project_plan(snapshot, rehearsal, manifest, mapper, cli, session / "project-logs", profile)
            # Project API only writes the disposable rehearsal. Start the transaction
            # rehearsal from the original destination snapshot, preserving API IDs.
            for n in (STATE, HISTORY):
                for suffix in ("", "-wal", "-shm"):
                    (rehearsal / (n + suffix)).unlink(missing_ok=True)
                backup_db(profile / n, rehearsal / n)
            pins = sidebar.get("pinned-thread-ids", [])
            try:
                apply_history(snapshot, rehearsal, manifest["session_id"], prepared, projects, assignments, mapper, pins, fail_after_threads=True, attachment_paths=attachment_paths)
            except MigrationError as e:
                if str(e) != "intentional-rollback-test":
                    raise
            else:
                raise MigrationError("回滚演练没有触发预期故障")
            print("正在演练恢复并核对原生历史…", flush=True)
            apply_history(snapshot, rehearsal, manifest["session_id"], prepared, projects, assignments, mapper, pins, attachment_paths=attachment_paths)
            try:
                proof = native_readback(snapshot, rehearsal, prepared, cli, session / "rehearsal-logs", profile, resume_samples=True, attachment_paths=attachment_paths)
            except MigrationError:
                export_references(snapshot, session / "references")
                state["issues"].append({"reason": "native_rehearsal_failed_references_preserved"})
                state.update(phase="partial", native_history_restored=False)
                save(state_path, state)
                write_report(session, state)
                return state
            save(plan_path, {"projects": projects, "assignments": assignments, "mapping": mapping, "proof": proof,
                             "cli": cli, "cli_version": cli_version, "rollback_test_passed": True})
        plan = load(plan_path)
        if plan["cli_version"] != cli_version:
            raise MigrationError("Codex CLI 在演练后发生变化，请重新检查兼容性")
        wait_closed(False)
        print("正在合并旧历史，保留新机已有聊天…", flush=True)
        result = apply_history(snapshot, profile, manifest["session_id"], prepared, plan["projects"], plan["assignments"], mapper,
                               sidebar.get("pinned-thread-ids", []), attachment_paths=attachment_paths)
        state.update(phase="history_committed", native_history_restored=True, history=result)
        save(state_path, state)
        state["sidebar"] = merge_sidebar(profile, sidebar, plan["projects"], plan["assignments"], plan["mapping"], set(prepared["included"]))
        save(state_path, state)
        print("正在读回恢复结果…", flush=True)
        state["native_readback"] = native_readback(snapshot, profile, prepared, cli, session / "readback-logs", profile, attachment_paths=attachment_paths)
        # Skills are discovered without executing their scripts or connecting accounts.
        rpc = RPC(cli, profile, session / "skill-logs", profile)
        try:
            scan = rpc.call("skills/list", {"cwds": [str(Path(home or Path.home()))], "forceReload": True})
            discovered = {str(Path(s["path"]).resolve()) for entry in scan.get("data", []) for s in entry.get("skills", []) if "path" in s}
            missing = [p for p in expected_skills if p not in discovered]
            errors = sum(len(x.get("errors", [])) for x in scan.get("data", []))
            state["skill_discovery"] = {"discovered": len(discovered), "restored_paths_missing": missing, "errors": errors, "scripts_executed": False}
            if missing or errors:
                state["issues"].append({"reason": "skill_discovery_incomplete", "missing": len(missing), "errors": errors})
        finally:
            rpc.close()
        expected_legacy = sidebar.get("thread-project-assignments", {})
        unmapped = sum(t in prepared["included"] and t not in plan["assignments"] for t in expected_legacy)
        if unmapped:
            state["issues"].append({"reason": "project_memberships_not_restored", "count": unmapped})
        state["issues"].extend({**x, "reason": "held_history_branch", "cause": x.get("reason")} for x in prepared["held"])
        state["limitations"] = ["external_accounts_and_skill_dependencies_not_executed", "requires_user_sidebar_visual_confirmation"]
        state["phase"] = "partial" if state["issues"] else "complete"
        d = connect_pair(profile)
        try:
            state["after_database_fingerprint"] = fingerprint(d)
        finally:
            d.close()
        state["after_global_sha256"] = sha(profile / ".codex-global-state.json")
        save(state_path, state)
        write_report(session, state)
        return state


def rollback_session(session, wait=False):
    session = Path(session).resolve()
    state = load(session / "state.json")
    if not state:
        raise MigrationError("未找到可回滚的迁移记录")
    profile = Path(state["profile"])
    with lock(session.parent / "migration.lock"):
        wait_closed(wait)
        original_global = Path(state["backup_root"]) / "profile/.codex-global-state.json"
        current_global = profile / ".codex-global-state.json"
        if state.get("native_history_restored"):
            same_as_before = original_global.is_file() and current_global.is_file() and load(original_global) == load(current_global)
            if not same_as_before and (not current_global.is_file() or sha(current_global) != state.get("after_global_sha256")):
                raise MigrationError("迁移后布局已有变化，拒绝覆盖，保留现有数据")
        index = profile / "attachments/pasted-text-attachments.json"
        original_index = Path(state["backup_root"]) / "profile/attachments/pasted-text-attachments.json"
        if state.get("attachment_index"):
            same_as_before = (not index.exists() and not original_index.exists()) or (index.is_file() and original_index.is_file() and load(index) == load(original_index))
            if not same_as_before and (not index.is_file() or sha(index) != state["attachment_index"]["after_sha256"]):
                raise MigrationError("迁移后的附件索引已有变化，保留现有内容")
        if state.get("native_history_restored"):
            if not state.get("after_database_fingerprint"):
                raise MigrationError("迁移尚未验收完成；请先继续恢复，不能盲目覆盖备份")
            rollback_databases(profile, Path(state["backup_root"]), state["after_database_fingerprint"])
            original = Path(state["backup_root"]) / "profile/.codex-global-state.json"
            if original.is_file():
                save(profile / ".codex-global-state.json", load(original))
        if state.get("attachment_index"):
            if original_index.is_file():
                save(index, load(original_index))
            else:
                index.unlink(missing_ok=True)
        state["rollback_files"] = [undo_tree(Path(op["destination"]), Path(op["receipt"])) for op in reversed(state["operations"])]
        state["phase"] = "rolled_back"
        save(session / "state.json", state)
        write_report(session, state)
    return state


def write_report(session, state):
    status = {"complete": "完整通过", "partial": "部分恢复，有待处理项", "rolled_back": "已回滚"}.get(state["phase"], "尚未恢复完成")
    issues = list(state["issues"])
    if state["phase"] == "complete" and not state.get("visual_confirmed"):
        status = "部分恢复，有待处理项"
        issues.append({"reason": "awaiting_user_sidebar_and_chat_confirmation"})
    summary = {"status": status, "session_id": state["session_id"], "phase": state["phase"],
               "history_threads": 0 if state["phase"] == "rolled_back" else len(state.get("history", {}).get("thread_ids", [])),
               "history_items": 0 if state["phase"] == "rolled_back" else state.get("native_readback", {}).get("items", 0),
               "held_history_threads": len(state.get("history", {}).get("held", [])),
               "backup": state.get("backup_root"), "issues": issues, "old_computer_deleted": False,
               "requires_visual_confirmation": not state.get("visual_confirmed", False),
               "limitations": state.get("limitations", [])}
    save(Path(session) / "result.json", summary)
    content = "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>迁移验收</title><style>body{max-width:760px;margin:50px auto;padding:24px;font:17px/1.7 system-ui;color:#20242b}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f5f7;padding:20px;border-radius:12px}</style>"
    content += "<h1>" + html.escape(status) + "</h1><p>旧电脑没有被清理，新机原有文件和聊天已保留。</p>"
    content += f'<p>已恢复 <strong>{summary["history_threads"]}</strong> 条历史线程（含内部子线程），核验 <strong>{summary["history_items"]}</strong> 个历史条目。</p>'
    if summary["held_history_threads"] and state["phase"] != "rolled_back":
        content += f'<p>另有 <strong>{summary["held_history_threads"]}</strong> 条会话暂缓原生导入，原件与参考资料已保留。</p>'
    if summary["requires_visual_confirmation"] and state["phase"] != "rolled_back":
        content += "<p>最后一步：打开 Codex，检查侧栏和一条旧聊天，然后回到助手窗口输入“确认”。</p>"
    labels = {
        "awaiting_user_sidebar_and_chat_confirmation": "等待你确认侧栏和旧聊天是否正常。",
        "existing_file_conflict_kept": "有同名文件内容不同：新机原件保留，旧版另存候选，请按需比较。",
        "custom_skill_version_kept_separately": "同名 Skill 有不同版本：新机原版保留，旧机改造版已作为独立入口恢复。",
        "held_history_branch": "部分会话未通过兼容性校验，已保留原件和参考资料，暂未原生导入。",
        "project_memberships_not_restored": "部分聊天尚未关联项目，原归属清单已保留，可后续补齐。",
        "local_attachment_not_resolved": "部分附件未在所选资料中找到可读原件，原引用已保留。",
        "skill_discovery_incomplete": "部分 Skill 尚未被客户端识别，需要检查入口或版本。",
        "invalid_skill_kept_as_reference": "部分 Skill 入口无法解析，已保留完整参考副本。",
        "native_rehearsal_failed_references_preserved": "原生历史演练未通过，未向新机聊天库合并；已导出参考资料。",
        "symlink_not_followed": "资料中有目录链接，未跟随复制；请查看详细记录后补齐。",
        "link_or_special_file_not_copied": "资料中有链接或特殊文件，未跟随复制；请查看详细记录。",
        "missing_or_external_rollout": "部分会话源文件缺失或位于用户目录之外，已登记待补齐。",
        "missing_history_database": "旧机未提供分页历史库，其他资料保留，聊天恢复尚未完成。",
    }
    friendly = []
    for issue in issues:
        reason = str(issue.get("reason", ""))
        label = labels.get(reason, reason if any('\u4e00' <= c <= '\u9fff' for c in reason) else "有迁移资料需要检查，详细信息保存在技术记录中。")
        if label not in friendly:
            friendly.append(label)
    if friendly:
        content += "<h2>待处理事项</h2><ul>" + "".join("<li>" + html.escape(x) + "</li>" for x in friendly) + "</ul>"
    content += "<p>外部账号、Skill 运行依赖及项目构建没有在此次恢复中执行。</p><p>保护备份：<code>" + html.escape(str(summary["backup"])) + "</code></p>"
    if state["phase"] == "partial":
        content += "<p>冲突候选和会话参考资料保存在本次迁移记录目录，可让 Skill 继续检查。</p>"
    content += "<details><summary>展开技术记录</summary><pre>" + html.escape(json.dumps(summary, ensure_ascii=False, indent=2)) + "</pre></details>"
    (Path(session) / "验收结果.html").write_text(content)


def confirm_visual(session):
    session = Path(session)
    state = load(session / "state.json")
    if not state or state["phase"] not in {"complete", "partial"}:
        raise MigrationError("迁移尚未处理完成，不能标记用户验收")
    state["visual_confirmed"] = True
    state["limitations"] = [x for x in state.get("limitations", []) if x != "requires_user_sidebar_visual_confirmation"]
    save(session / "state.json", state)
    write_report(session, state)
    return load(session / "result.json")
