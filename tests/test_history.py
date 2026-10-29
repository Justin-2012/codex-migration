from codex_migration.common import connect
import json
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from codex_migration.common import MigrationError, backup_db
from codex_migration.history import (compatible, apply_history, prepare_rollouts, sanitize_line,
                                     fingerprint, connect_pair, rollback_databases, STATE, HISTORY)
from fixtures import profile, thread


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = profile(self.root / "snapshot/codex", 62)
        self.dest = profile(self.root / "destination")
        self.old, rollout = thread(self.source, "早期历史")
        self.current, _ = thread(self.dest, "本机已有内容")
        self.manifest = {"rollouts": {self.old: "codex/" + str(rollout.relative_to(self.source))}}
        self.prepared = prepare_rollouts(self.source.parent, self.root / "safe", self.manifest, lambda p: str(self.root))
        self.session = str(uuid.uuid4())

    def fingerprint(self):
        d = connect_pair(self.dest)
        try:
            return fingerprint(d)
        finally:
            d.close()

    def apply(self, **kwargs):
        return apply_history(self.source.parent, self.dest, self.session, self.prepared, [], {}, lambda p: str(self.root), **kwargs)

    def test_additive_transaction_and_repeat_are_safe(self):
        a = self.apply()
        self.assertEqual(a, self.apply())
        self.assertTrue(a["existing_rows_preserved"])
        with connect(self.dest / STATE) as d:
            self.assertEqual(d.execute("select count(*) from threads").fetchone()[0], 2)
            self.assertEqual(d.execute("select approval_mode from threads where id=?", (self.old,)).fetchone()[0], "on-request")

    def test_duplicate_roots_reusing_one_project_id_do_not_double_insert(self):
        pid = str(uuid.uuid4())
        project = {"id": pid, "name": "one root", "position": 1, "created_at_ms": 1, "updated_at_ms": 1, "roots": [str(self.root)]}
        entries = [dict(project, old_id="source-a"), dict(project, old_id="source-b")]
        result = apply_history(self.source.parent, self.dest, self.session, self.prepared, entries, {}, lambda p: str(self.root))
        self.assertEqual(result["created_projects"], [pid])
        with connect(self.dest / STATE) as d:
            self.assertEqual(d.execute("select count(*) from projects").fetchone()[0], 1)

    def test_failure_rolls_back_both_databases(self):
        before = self.fingerprint()
        with self.assertRaisesRegex(MigrationError, "intentional"):
            self.apply(fail_after_threads=True)
        self.assertEqual(before, self.fingerprint())

    def test_real_rollback_restores_existing_data(self):
        backup = self.root / "backup"
        for n in (STATE, HISTORY):
            backup_db(self.dest / n, backup / n)
        before = self.fingerprint()
        self.apply()
        rollback_databases(self.dest, backup, self.fingerprint())
        self.assertEqual(before, self.fingerprint())

    def test_rollback_refuses_to_destroy_new_chat(self):
        self.apply()
        after = self.fingerprint()
        thread(self.dest, "迁移后新消息")
        with self.assertRaisesRegex(MigrationError, "新的数据库变化"):
            rollback_databases(self.dest, self.root, after)

    def test_unknown_schema_never_writes(self):
        with connect(self.dest / STATE) as d:
            d.execute("update _sqlx_migrations set version=999")
        before = self.fingerprint()
        self.assertFalse(compatible(self.source, self.dest)[0])
        with self.assertRaises(MigrationError):
            self.apply()
        self.assertEqual(before, self.fingerprint())

    def test_same_version_but_changed_structure_is_rejected(self):
        with connect(self.dest / STATE) as d:
            d.execute("alter table threads add column future text")
        self.assertFalse(compatible(self.source, self.dest)[0])

    def test_reset_preserves_offsets_and_response_bytes(self):
        response = b'{"type":"response_item","payload":{"text":"original"}}\n'
        self.assertEqual(sanitize_line(response, "/new"), response)
        original = (self.source / "sessions" / (self.old + ".jsonl")).read_bytes()
        sanitized = sanitize_line(original, "/new")
        self.assertEqual(len(original), len(sanitized))
        self.assertEqual(json.loads(sanitized)["payload"]["cwd"], "/new")

    def test_unverified_branch_held_without_changing_original(self):
        rollout = self.source / "sessions" / (self.old + ".jsonl")
        x = json.loads(rollout.read_bytes())
        x["payload"]["history_base"] = {"thread_id": "missing-parent"}
        rollout.write_text(json.dumps(x))
        before = rollout.read_bytes()
        result = prepare_rollouts(self.source.parent, self.root / "other", self.manifest, lambda p: "/new")
        self.assertFalse(result["included"])
        self.assertEqual(len(result["held"]), 1)
        self.assertEqual(before, rollout.read_bytes())
