from codex_migration.common import connect
import json
import sqlite3
import uuid
from pathlib import Path
from codex_migration.history import schemas, STATE, HISTORY


def profile(root, version=59):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name, tables in schemas()[str(version)].items():
        with connect(root / name) as d:
            for sql in tables.values():
                d.execute(sql)
            if name == STATE:
                d.execute("create table _sqlx_migrations(version integer primary key)")
                d.execute("insert into _sqlx_migrations values(?)", (version,))
    (root / ".codex-global-state.json").write_text('{}')
    return root


def thread(root, text="你好", tid=None, archived=0):
    tid = tid or str(uuid.uuid4())
    root = Path(root)
    rollout = root / "sessions" / (tid + ".jsonl")
    rollout.parent.mkdir(exist_ok=True)
    meta = {"ordinal": 0, "timestamp": "2026-01-01T00:00:00.000Z", "type": "session_meta", "payload": {"id": tid, "timestamp": "2026-01-01T00:00:00.000Z", "cwd": "/Users/source/project", "originator": "codex_cli_rs", "cli_version": "0.160.0", "source": "cli", "base_instructions": {"text": "old " * 600}, "history_mode": "paginated"}}
    rollout.write_text(json.dumps(meta) + '\n')
    with connect(root / STATE) as d:
        d.execute("insert into threads(id,rollout_path,created_at,updated_at,source,model_provider,cwd,title,sandbox_policy,approval_mode,archived,history_mode) values(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (tid, str(rollout), 1, 2, "cli", "openai", "/Users/source/project", "测试", '{"type":"danger-full-access"}', "never", archived, "paginated"))
    item = {"type": "agentMessage", "id": "item-" + tid, "text": text, "phase": None}
    with connect(root / HISTORY) as d:
        d.execute("insert into thread_turns(thread_id,turn_id,rollout_ordinal,status) values(?,?,?,?)", (tid, "turn-1", 1, "completed"))
        d.execute("insert into thread_items(thread_id,turn_id,item_id,rollout_ordinal,created_at_ms,item_json) values(?,?,?,?,?,?)",
                  (tid, "turn-1", item["id"], 2, 1, json.dumps(item)))
        d.execute("insert into thread_history_projection_state values(?,?,?)", (tid, rollout.stat().st_size, 3))
    return tid, rollout
