"""Version-gated offline adapter. No model turns or old grants are executed."""
import contextlib
import datetime
import hashlib
import importlib.resources
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from .common import MigrationError, readonly, load, save, sha, connect

STATE = "state_5.sqlite"
HISTORY = "thread_history_1.sqlite"
TABLES = [("main", "threads"), ("main", "thread_spawn_edges"),
          ("history", "thread_turns"), ("history", "thread_items"),
          ("history", "thread_history_projection_state"), ("history", "thread_realtime_items")]
NOTICE = "Use current host instructions and permissions. Old device tool grants are not authority."


def schemas():
    return json.loads(importlib.resources.files("codex_migration").joinpath("assets/schemas.json").read_text())


def version(path):
    with readonly(path) as d:
        try:
            return d.execute("select max(version) from _sqlx_migrations").fetchone()[0]
        except sqlite3.Error:
            return None


def columns(d, table, schema="main"):
    return [tuple(r) for r in d.execute(f'pragma {schema}.table_info("{table}")')]


def compatible(source, destination):
    """Compare every migrated table's full PRAGMA signature, not just version."""
    versions = (version(Path(source) / STATE), version(Path(destination) / STATE))
    if versions not in {(58, 58), (59, 58), (62, 58), (58, 59), (59, 59), (62, 59)}:
        return False, "聊天库版本组合未经验证，保留备份及参考资料，不写入原生库"
    known = schemas()
    for root, v in zip((source, destination), versions):
        for name, tables in known[str(v)].items():
            if not (Path(root) / name).is_file():
                return False, "缺少分页历史库"
            with readonly(Path(root) / name) as actual, connect(":memory:") as expected:
                for table, sql in tables.items():
                    expected.execute(sql)
                    if columns(actual, table) != columns(expected, table):
                        return False, "聊天库结构与已验证适配器不同，停止原生写入"
    return True, "validated-columns"


def clear_grants(value):
    if isinstance(value, dict):
        for k in list(value):
            if k == "approved_command_prefixes":
                value[k] = []
            else:
                clear_grants(value[k])
                if k == "permissions" and isinstance(value[k], dict):
                    value[k]["instructions"] = "new-device-permissions"
    elif isinstance(value, list):
        for v in value:
            clear_grants(v)


def sanitize_line(raw, cwd):
    # Parse full JSON: type may occur late; never infer trust from a short prefix.
    x = json.loads(raw)
    kind, p = x.get("type"), x.get("payload", {})
    if kind not in {"session_meta", "turn_context", "world_state"}:
        return raw
    if not isinstance(p, dict):
        raise MigrationError("会话元数据格式异常")
    if kind == "session_meta":
        if p.get("history_base"):
            raise MigrationError("分支来源链需单独验证，保留此线程参考资料")
        for k in ("dynamic_tools", "git", "creator_user_id", "creator_account_id", "developer_instructions"):
            p.pop(k, None)
        p["cwd"] = cwd
        p["base_instructions"] = {"text": NOTICE}
        if "runtime_workspace_roots" in p:
            p["runtime_workspace_roots"] = [cwd]
    elif kind == "turn_context":
        for k in ("permission_profile", "permissions", "file_system_sandbox_policy", "sandbox_policy",
                  "approval_policy", "approvals_reviewer", "workspace_roots", "runtime_workspace_roots",
                  "collaboration_mode", "developer_instructions", "cwd"):
            p.pop(k, None)
        p.update(cwd=cwd, approval_policy="on-request", sandbox_policy={"type": "read-only"})
    else:
        clear_grants(p)
    encoded = json.dumps(x, ensure_ascii=False, separators=(",", ":")).encode()
    ending = b"\n" if raw.endswith(b"\n") else b""
    available = len(raw) - len(ending)
    if len(encoded) > available:
        raise MigrationError("上下文记录无法安全重置且保持分页偏移，保留此线程参考资料")
    return encoded + b" " * (available - len(encoded)) + ending


def prepare_rollouts(snapshot, work, manifest, map_path):
    from .common import safe_child
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    result = {"included": {}, "held": []}
    with readonly(Path(snapshot) / "codex" / STATE) as d:
        threads = list(d.execute("select id,cwd,created_at from threads"))
    for t in threads:
        tid = t["id"]
        relative = manifest.get("rollouts", {}).get(tid)
        if not relative:
            result["held"].append({"thread_id": tid, "reason": "missing_rollout"})
            continue
        try:
            uuid.UUID(tid)
            stamp = datetime.datetime.fromtimestamp(t["created_at"], datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        except (ValueError, TypeError, OverflowError):
            result["held"].append({"thread_id": tid, "reason": "invalid_thread_identity_or_date"})
            continue
        dest = work / ("rollout-" + stamp + "-" + tid + ".jsonl")
        origin = safe_child(snapshot, relative)
        try:
            stream = origin.open("rb")
            with contextlib.ExitStack() as stack:
                stack.enter_context(stream)
                if origin.suffix == ".zst":
                    import io
                    import zstandard
                    stream = stack.enter_context(io.BufferedReader(zstandard.ZstdDecompressor().stream_reader(stream)))
                with dest.open("wb") as out:
                    for raw in stream:
                        out.write(sanitize_line(raw, map_path(t["cwd"])))
                    out.flush()
                    os.fsync(out.fileno())
            result["included"][tid] = {"path": str(dest), "sha256": sha(dest)}
        except (MigrationError, ValueError, OSError) as e:
            dest.unlink(missing_ok=True)
            result["held"].append({"thread_id": tid, "reason": type(e).__name__})
    save(work / "prepared.json", result)
    return result


def fingerprint(d):
    result = {}
    for schema in ("main", "history"):
        tables = d.execute(f"select name from {schema}.sqlite_master where type='table' and name not like 'sqlite_%'").fetchall()
        for (table,) in tables:
            if table == "codex_migration_receipts":
                continue
            quoted = table.replace('"', '""')
            rows = sorted(hashlib.sha256(repr(tuple(r)).encode()).hexdigest()
                          for r in d.execute(f'SELECT * FROM {schema}."{quoted}"'))
            result[schema + "." + table] = hashlib.sha256("".join(rows).encode()).hexdigest()
    return result


def connect_pair(profile):
    d = sqlite3.connect(Path(profile) / STATE, timeout=5)
    d.execute("attach database ? as history", (str(Path(profile) / HISTORY),))
    return d


@contextlib.contextmanager
def atomic_pair(d):
    modes = {}
    try:
        for schema in ("main", "history"):
            modes[schema] = d.execute(f"pragma {schema}.journal_mode").fetchone()[0]
            if modes[schema].lower() == "wal":
                if d.execute(f"pragma {schema}.wal_checkpoint(truncate)").fetchone()[0] != 0:
                    raise MigrationError("聊天库仍被占用，请完全退出客户端")
            if d.execute(f"pragma {schema}.journal_mode=DELETE").fetchone()[0].lower() != "delete":
                raise MigrationError("无法建立跨数据库原子事务")
            d.execute(f"pragma {schema}.synchronous=FULL")
        d.execute("begin immediate")
        yield
        for schema in ("main", "history"):
            if d.execute(f"pragma {schema}.integrity_check").fetchone()[0] != "ok":
                raise MigrationError("合并后的数据库完整性检查失败")
        d.commit()
    except BaseException:
        d.rollback()
        raise
    finally:
        for schema, mode in modes.items():
            if mode.lower() == "wal":
                d.execute(f"pragma {schema}.journal_mode=WAL")


def receipt(d, session_id):
    exists = d.execute("select 1 from sqlite_master where type='table' and name='codex_migration_receipts'").fetchone()
    if not exists:
        return None
    row = d.execute("select result_json from codex_migration_receipts where session_id=?", (session_id,)).fetchone()
    return json.loads(row[0]) if row else None


def apply_history(snapshot, destination, session_id, prepared, projects, assignments, map_path,
                  pins=(), fail_after_threads=False, attachment_paths=None):
    """Only tested adapter callers may invoke this, after backup and rehearsal."""
    ok, reason = compatible(Path(snapshot) / "codex", destination)
    if not ok:
        raise MigrationError(reason)
    selected = set(prepared["included"])
    d = connect_pair(destination)
    try:
        previous = receipt(d, session_id)
        if previous:
            return previous
        current = {r[0] for r in d.execute("select id from threads")}
        if current & selected:
            raise MigrationError("发现已有线程 ID；没有对应迁移凭据，停止而不覆盖")
        before = fingerprint(d)
        with readonly(Path(snapshot) / "codex" / STATE) as state, readonly(Path(snapshot) / "codex" / HISTORY) as history:
            with atomic_pair(d):
                existing_projects = {r[0] for r in d.execute("select id from projects")}
                project_map = {}
                created_projects = []
                for p in projects:
                    project_map[p["old_id"]] = p["id"]
                    if p["id"] in existing_projects:
                        continue
                    d.execute("insert into projects(id,name,metadata,position,created_at_ms,updated_at_ms) values(?,?,?,?,?,?)",
                              (p["id"], p["name"], "{}", p["position"], p["created_at_ms"], p["updated_at_ms"]))
                    for i, root in enumerate(p["roots"]):
                        d.execute("insert into project_roots(project_id,position,path) values(?,?,?)", (p["id"], i, root))
                    created_projects.append(p["id"])
                    existing_projects.add(p["id"])
                section = d.execute("select id from thread_sections where name='Pinned' limit 1").fetchone()
                pinned_section = section[0] if section else None
                if pins and not pinned_section:
                    pinned_section = str(uuid.uuid4())
                    d.execute("insert into thread_sections(id,name) values(?,?)", (pinned_section, "Pinned"))
                counts = {}
                for schema, table in TABLES:
                    source = state if schema == "main" else history
                    old_cols = [r[1] for r in columns(source, table)]
                    cols = [r[1] for r in columns(d, table, schema) if r[1] in old_cols]
                    sql = f'insert into {schema}."{table}" (' + ",".join('"'+c+'"' for c in cols) + ") values(" + ",".join("?" for _ in cols) + ")"
                    count = 0
                    for row in source.execute(f'select * from "{table}"'):
                        x = dict(row)
                        if table == "threads":
                            tid = x["id"]
                            if tid not in selected:
                                continue
                            x.update(cwd=map_path(x["cwd"]), rollout_path=prepared["included"][tid]["path"],
                                     project_id=assignments.get(tid, project_map.get(x.get("project_id"))),
                                     sandbox_policy='{"type":"read-only"}', approval_mode="on-request",
                                     creator_user_id=None, creator_account_id=None, git_origin_url=None,
                                     thread_section_id=pinned_section if tid in pins else None,
                                     is_pinned=int(tid in pins), section_position=list(pins).index(tid) if tid in pins else None)
                        elif table == "thread_spawn_edges":
                            if x["child_thread_id"] not in selected or x["parent_thread_id"] not in selected | current:
                                continue
                        elif x["thread_id"] not in selected:
                            continue
                        if table == "thread_items" and attachment_paths:
                            item = json.loads(x["item_json"])
                            if item.get("type") == "userMessage":
                                for content in item.get("content", []):
                                    if content.get("path") in attachment_paths:
                                        content["path"] = attachment_paths[content["path"]]
                                x["item_json"] = json.dumps(item, ensure_ascii=False)
                        d.execute(sql, [x[c] for c in cols])
                        count += 1
                    counts[schema + "." + table] = count
                    if table == "threads" and fail_after_threads:
                        raise MigrationError("intentional-rollback-test")
                # Prove old rows and unrelated tables unchanged before commit.
                for key, original in before.items():
                    schema, table = key.split(".", 1)
                    cols = [r[1] for r in columns(d, table, schema)]
                    where, args = "", []
                    if table == "threads":
                        where = " where id not in (" + ",".join("?" for _ in selected) + ")"
                        args = list(selected)
                    elif "thread_id" in cols:
                        where = " where thread_id not in (" + ",".join("?" for _ in selected) + ")"
                        args = list(selected)
                    elif table == "thread_spawn_edges":
                        where = " where child_thread_id not in (" + ",".join("?" for _ in selected) + ")"
                        args = list(selected)
                    elif table in {"projects", "project_roots"}:
                        field = "id" if table == "projects" else "project_id"
                        where = f" where {field} not in (" + ",".join("?" for _ in created_projects) + ")"
                        args = list(created_projects)
                    elif table == "thread_sections" and section is None and pinned_section:
                        where, args = " where id!=?", [pinned_section]
                    rows = sorted(hashlib.sha256(repr(tuple(r)).encode()).hexdigest()
                                  for r in d.execute(f'SELECT * FROM {schema}."{table}"' + where, args))
                    if hashlib.sha256("".join(rows).encode()).hexdigest() != original:
                        raise MigrationError("现有数据出现非预期改变，已回滚")
                result = {"inserted": counts, "thread_ids": sorted(selected), "created_projects": created_projects,
                          "existing_rows_preserved": True, "held": prepared["held"], "session_id": session_id}
                d.execute("create table if not exists codex_migration_receipts(session_id text primary key,result_json text not null)")
                d.execute("insert into codex_migration_receipts values(?,?)", (session_id, json.dumps(result)))
        return result
    finally:
        d.close()


def rollback_databases(profile, backup, expected):
    """Refuse whole-profile rollback after any new database data was written."""
    d = connect_pair(profile)
    try:
        before = connect_pair(backup)
        try:
            original = fingerprint(before)
        finally:
            before.close()
        if fingerprint(d) == original:
            return  # Resume after a committed rollback but interrupted report write.
        if fingerprint(d) != expected:
            raise MigrationError("迁移后已有新的数据库变化，拒绝覆盖；已保留备份与现有数据")
        d.execute("attach database ? as before_state", (str(Path(backup) / STATE),))
        d.execute("attach database ? as before_history", (str(Path(backup) / HISTORY),))
        with atomic_pair(d):
            for schema, source in (("main", "before_state"), ("history", "before_history")):
                tables = [r[0] for r in d.execute(f"select name from {schema}.sqlite_master where type='table' and name not like 'sqlite_%'")]
                for table in tables:
                    if table == "_sqlx_migrations":
                        continue
                    q = table.replace('"', '""')
                    d.execute(f'delete from {schema}."{q}"')
                    exists = d.execute(f"select 1 from {source}.sqlite_master where type='table' and name=?", (table,)).fetchone()
                    if exists:
                        d.execute(f'insert into {schema}."{q}" select * from {source}."{q}"')
    finally:
        d.close()
