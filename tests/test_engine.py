import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fixtures import profile, thread
from codex_migration.bundle import make_snapshot
from codex_migration.engine import restore_snapshot, rollback_session
from codex_migration.common import load, sha
from codex_migration.files import restore_tree, undo_tree


class EngineTest(unittest.TestCase):
    def test_unknown_runtime_restores_files_exports_history_preserves_destination_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            old, new = base / "old", base / "new"
            source = profile(old / ".codex")
            destination = profile(new / ".codex")
            thread(source, "旧历史")
            thread(destination, "新机已有历史")
            project = old / "项目"
            project.mkdir()
            (project / "new.txt").write_text("补入")
            (project / "conflict.txt").write_text("旧内容")
            target = new / "项目"
            target.mkdir()
            (target / "conflict.txt").write_text("保留本机")
            make_snapshot(source, [{"path": str(project)}], base / "snapshot", old)
            before = sha(destination / "state_5.sqlite")
            with patch("codex_migration.engine.wait_closed"), patch("codex_migration.engine.find_cli", return_value=(None, "unsupported")):
                result = restore_snapshot(base / "snapshot", destination, base / "storage", home=new, authorize_history=True)
                repeat = restore_snapshot(base / "snapshot", destination, base / "storage", home=new, authorize_history=True)
                self.assertEqual(result, repeat)
                self.assertEqual(result["phase"], "partial")
                self.assertEqual(sha(destination / "state_5.sqlite"), before)
                self.assertEqual((target / "conflict.txt").read_text(), "保留本机")
                self.assertEqual((target / "new.txt").read_text(), "补入")
                session = base / "storage" / result["session_id"]
                self.assertTrue(list((session / "references").glob("*.jsonl")))
                rollback_session(session)
                self.assertFalse((target / "new.txt").exists())
                self.assertEqual((target / "conflict.txt").read_text(), "保留本机")

    def test_interruption_between_file_publish_and_receipt_recovers(self):
        import os
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            source = root / "source"
            source.mkdir()
            (source / "new").write_text("owned")
            link = os.link
            def power_loss(a, b):
                link(a, b)
                raise RuntimeError("power-loss")
            with patch("codex_migration.files.os.link", side_effect=power_loss):
                with self.assertRaisesRegex(RuntimeError, "power-loss"):
                    restore_tree(source, root / "target", root / "receipt.json", root / "conflicts")
            result = restore_tree(source, root / "target", root / "receipt.json", root / "conflicts")
            self.assertEqual(len(result["created"]), 1)
            self.assertEqual(result["intents"], [])
            undo_tree(root / "target", root / "receipt.json")
            self.assertFalse((root / "target/new").exists())

    def test_rollback_keeps_user_modified_addition(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "source").mkdir()
            (root / "source/file").write_text("old")
            restore_tree(root / "source", root / "target", root / "receipt", root / "conflicts")
            (root / "target/file").write_text("user edited")
            result = undo_tree(root / "target", root / "receipt")
            self.assertEqual(result["kept_modified"], ["file"])
