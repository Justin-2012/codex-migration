import json
import tempfile
import unittest
from pathlib import Path
from codex_migration.bundle import make_snapshot, verify_snapshot
from codex_migration.common import MigrationError, save
from fixtures import profile, thread


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.profile = profile(self.home / ".codex")
        thread(self.profile)

    def test_snapshot_includes_nested_manifest_and_excludes_auth(self):
        project = self.home / "项目 空格"
        project.mkdir()
        (project / "manifest.json").write_text('{}')
        (self.profile / "auth.json").write_text('private')
        snapshot = self.home / "snapshot"
        make_snapshot(self.profile, [{"path": str(project)}], snapshot, self.home)
        self.assertTrue(verify_snapshot(snapshot))
        self.assertFalse(any(p.name == "auth.json" for p in snapshot.rglob("*")))

    def test_truncated_cloud_file_is_not_marked_complete(self):
        snapshot = self.home / "snapshot"
        manifest = make_snapshot(self.profile, [], snapshot, self.home)
        (snapshot / manifest["files"][0]["path"]).write_text('incomplete')
        with self.assertRaisesRegex(MigrationError, "完整下载|校验"):
            verify_snapshot(snapshot)

    def test_path_traversal_manifest_is_rejected(self):
        snapshot = self.home / "snapshot"
        manifest = make_snapshot(self.profile, [], snapshot, self.home)
        manifest["files"][0]["path"] = "../private"
        save(snapshot / "manifest.json", manifest)
        with self.assertRaises(MigrationError):
            verify_snapshot(snapshot)

    def test_project_key_cannot_escape_target_root(self):
        snapshot = self.home / "snapshot"
        manifest = make_snapshot(self.profile, [], snapshot, self.home)
        manifest["projects"] = [{"key": "../../private", "snapshot": "projects/../../private", "old_path": "/external/folder"}]
        save(snapshot / "manifest.json", manifest)
        with self.assertRaises(MigrationError):
            verify_snapshot(snapshot)

    def test_external_git_worktree_not_silently_packaged(self):
        project = self.home / "worktree"
        project.mkdir()
        (project / ".git").write_text('gitdir: /external/private')
        with self.assertRaisesRegex(MigrationError, "外部 Git"):
            make_snapshot(self.profile, [{"path": str(project)}], self.home / "snapshot", self.home)
