import tempfile
import unittest
from pathlib import Path
from codex_migration.files import snapshot_tree, restore_tree


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()

    def test_keeps_git_and_uncommitted_files_but_excludes_credentials(self):
        (self.source / ".git").mkdir()
        (self.source / ".git/HEAD").write_text("ref: refs/heads/main\n")
        (self.source / "未提交.txt").write_text("keep")
        (self.source / ".env").write_text("private")
        (self.source / "node_modules").mkdir()
        (self.source / "node_modules/cache").write_text("rebuild")
        snapshot_tree(self.source, self.root / "copy")
        self.assertEqual((self.root / "copy/.git/HEAD").read_text(), "ref: refs/heads/main\n")
        self.assertTrue((self.root / "copy/未提交.txt").is_file())
        self.assertFalse((self.root / "copy/.env").exists())
        self.assertFalse((self.root / "copy/node_modules").exists())

    def test_external_symlink_is_reported_and_never_followed(self):
        (self.root / "private").write_text("outside")
        (self.source / "external").symlink_to(self.root / "private")
        result = snapshot_tree(self.source, self.root / "copy")
        self.assertTrue(result["issues"])
        self.assertFalse((self.root / "copy/external").exists())

    def test_preserves_conflict_and_does_not_duplicate_on_resume(self):
        (self.source / "same").write_text("same")
        (self.source / "conflict").write_text("old")
        (self.source / "new").write_text("new")
        destination = self.root / "destination"
        destination.mkdir()
        (destination / "same").write_text("same")
        (destination / "conflict").write_text("current")
        receipt = self.root / "receipt.json"
        conflicts = self.root / "conflicts"
        a = restore_tree(self.source, destination, receipt, conflicts)
        z = restore_tree(self.source, destination, receipt, conflicts)
        self.assertEqual((destination / "conflict").read_text(), "current")
        self.assertEqual((conflicts / "conflict").read_text(), "old")
        self.assertEqual(a["created"], z["created"])
        self.assertEqual(len(z["created"]), 1)
