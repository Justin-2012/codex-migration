import tempfile
import unittest
from pathlib import Path
from codex_migration.skills import skill_operations
from codex_migration.files import restore_tree


class SkillTest(unittest.TestCase):
    def test_conflicting_tree_restored_as_coherent_variant(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            source, target = root / "old/my-skill", root / "new/my-skill"
            for folder in (source, target):
                folder.mkdir(parents=True)
                (folder / "SKILL.md").write_text('---\nname: my-skill\ndescription: Example\n---\nCustom instructions\n')
            (source / "script.py").write_text("old customization")
            (target / "script.py").write_text("current")
            (source / "only-old.py").write_text("do not mix")
            operations, issues, expected = skill_operations(source.parent, target.parent, root / "stage", "12345678-session")
            self.assertTrue(issues)
            origin, dest, _ = operations[0]
            restore_tree(origin, dest, root / "receipt", root / "conflicts")
            self.assertEqual((target / "script.py").read_text(), "current")
            self.assertFalse((target / "only-old.py").exists())
            self.assertEqual((dest / "only-old.py").read_text(), "do not mix")
            self.assertIn(dest.name, (dest / "SKILL.md").read_text())

    def test_variant_name_stable_across_mount_points(self):
        import shutil
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for folder in (root / "mount1/custom", root / "target/custom"):
                folder.mkdir(parents=True)
                (folder / "SKILL.md").write_text('---\nname: custom\ndescription: Custom\n---\nRules\n')
            (root / "mount1/custom/script").write_text("old")
            (root / "target/custom/script").write_text("new")
            shutil.copytree(root / "mount1", root / "mount2")
            a = skill_operations(root / "mount1", root / "target", root / "stage1", "12345678")[0][0][1]
            z = skill_operations(root / "mount2", root / "target", root / "stage2", "12345678")[0][0][1]
            self.assertEqual(a, z)

    def test_two_sources_do_not_mix_pending_versions(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for area, body in (("a", "first"), ("b", "second")):
                p = root / area / "custom"
                p.mkdir(parents=True)
                (p / "SKILL.md").write_text('---\nname: custom\ndescription: Custom\n---\nRules\n')
                (p / "script").write_text(body)
            reserved = {}
            a = skill_operations(root / "a", root / "target", root / "stage-a", "12345678", reserved)[0][0][1]
            b = skill_operations(root / "b", root / "target", root / "stage-b", "12345678", reserved)[0][0][1]
            self.assertNotEqual(a, b)
