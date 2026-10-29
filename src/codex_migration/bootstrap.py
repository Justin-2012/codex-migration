from .common import connect
"""Let the validated native backend initialize a missing, empty history store."""
import datetime
import json
import shutil
import sqlite3
import uuid
from pathlib import Path
from .common import MigrationError, backup_db, readonly
from .history import STATE, HISTORY
from .rpc import RPC


def empty_history(profile, work, cli):
    profile, work = Path(profile), Path(work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    backup_db(profile / STATE, work / STATE)
    tid = str(uuid.uuid4())
    stamp = "2000-01-01T00-00-00"
    rollout = work / ("rollout-" + stamp + "-" + tid + ".jsonl")
    rollout.write_text(json.dumps({"ordinal": 0, "timestamp": "2000-01-01T00:00:00Z", "type": "session_meta", "payload": {
        "id": tid, "timestamp": "2000-01-01T00:00:00Z", "cwd": str(work), "originator": "codex_cli_rs", "cli_version": "0.160.0", "source": "cli",
        "history_mode": "paginated", "base_instructions": {"text": "Initialize an empty migration history store. No turn starts."}}}) + "\n")
    with connect(work / STATE) as d:
        d.execute("insert into threads(id,rollout_path,created_at,updated_at,source,model_provider,cwd,title,sandbox_policy,approval_mode,history_mode) values(?,?,?,?,?,?,?,?,?,?,?)",
                  (tid, str(rollout), 1, 1, "cli", "openai", str(work), "migration initializer", '{"type":"read-only"}', "on-request", "paginated"))
    rpc = RPC(cli, work, work / "logs", work)
    try:
        rpc.call("thread/items/list", {"threadId": tid, "limit": 10, "sortDirection": "asc"})
    finally:
        rpc.close()
    with connect(work / HISTORY) as d:
        for table in ("thread_turns", "thread_items", "thread_history_projection_state", "thread_realtime_items"):
            d.execute("delete from " + table)
    with readonly(work / HISTORY) as d:
        if d.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise MigrationError("原生空历史库初始化失败")
    return work / HISTORY
