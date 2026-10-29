import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from codex_migration.common import MigrationError, require_space, save, connect
from codex_migration.attachments import merge_text_index
from codex_migration.history import apply_history, prepare_rollouts
from fixtures import profile, thread


class SafetyTest(unittest.TestCase):
    def test_rollback_report_does_not_claim_history_still_restored(self):
        from codex_migration.engine import write_report
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            state = {"session_id": "synthetic", "phase": "rolled_back", "issues": [], "history": {"thread_ids": ["old"]}, "native_readback": {"items": 10}}
            write_report(root, state)
            report = json.loads((root / "result.json").read_text())
            self.assertEqual(report["history_threads"], 0)
            self.assertEqual(report["history_items"], 0)

    def test_sidebar_sorting_restores_only_known_display_preferences(self):
        from codex_migration.engine import merge_sidebar
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            save(root / ".codex-global-state.json", {"electron-persisted-atom-state": {"flat-project-sidebar-preferences-v1": {"keep": "current"}}, "other": "preserve"})
            merge_sidebar(root, {"sorting-preferences-v1": {"chatSortMode": "manual", "projectSortMode": "manual", "approvalPolicy": "never"}}, [], {}, {}, set())
            value = json.loads((root / ".codex-global-state.json").read_text())
            preferences = value["electron-persisted-atom-state"]["flat-project-sidebar-preferences-v1"]
            self.assertEqual(preferences["keep"], "current")
            self.assertEqual(preferences["chatSortMode"], "manual")
            self.assertNotIn("approvalPolicy", preferences)
            self.assertEqual(value["other"], "preserve")

    def test_running_client_stops_before_mutation(self):
        from codex_migration.platform import wait_closed
        with patch("codex_migration.platform.clients_running", return_value=True):
            with self.assertRaisesRegex(MigrationError, "先完全退出"):
                wait_closed(False)

    def test_unknown_architecture_not_marked_supported(self):
        from codex_migration.platform import check_platform
        with patch("codex_migration.platform.platform.system", return_value="Darwin"), patch("codex_migration.platform.platform.machine", return_value="x86_64"):
            with self.assertRaises(MigrationError):
                check_platform()

    def test_insufficient_space_stops_before_copy(self):
        with patch("codex_migration.common.shutil.disk_usage") as usage:
            usage.return_value.free = 5
            with self.assertRaisesRegex(MigrationError, "空间不足"):
                require_space(Path.cwd(), 100)

    def test_existing_id_without_atomic_receipt_is_not_overwritten(self):
        import uuid
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            source = profile(root / "snapshot/codex")
            target = profile(root / "target")
            tid, rollout = thread(source, "old")
            thread(target, "keep", tid=tid)
            prepared = prepare_rollouts(source.parent, root / "safe", {"rollouts": {tid: "codex/" + str(rollout.relative_to(source))}}, lambda p: str(root))
            with self.assertRaisesRegex(MigrationError, "已有线程"):
                apply_history(source.parent, target, str(uuid.uuid4()), prepared, [], {}, lambda p: str(root))
            with connect(target / "thread_history_1.sqlite") as d:
                item = json.loads(d.execute("select item_json from thread_items").fetchone()[0])
                self.assertEqual(item["text"], "keep")

    def test_attachment_index_preserves_current_removals_and_excerpts(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            source, dest = root / "snapshot/attachments", root / "profile/attachments"
            source.mkdir(parents=True)
            dest.mkdir(parents=True)
            for folder in (source, dest):
                (folder / "a.txt").write_text("same")
            old = "/Users/example/.codex/attachments/a.txt"
            new = str(dest / "a.txt")
            save(source / "pasted-text-attachments.json", {"attachmentPaths": [old], "pendingRemovalPaths": [old], "textExcerptsByPath": {old: "old excerpt"}})
            save(dest / "pasted-text-attachments.json", {"attachmentPaths": [], "pendingRemovalPaths": ["current-removal"], "textExcerptsByPath": {new: "current excerpt"}})
            merge_text_index(root / "snapshot", {"source_profile": "/Users/example/.codex"}, root / "profile")
            value = json.loads((dest / "pasted-text-attachments.json").read_text())
            self.assertEqual(value["pendingRemovalPaths"], ["current-removal"])
            self.assertEqual(value["textExcerptsByPath"][new], "current excerpt")
            self.assertEqual(value["attachmentPaths"], [new])
