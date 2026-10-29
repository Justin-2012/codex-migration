"""Synthetic dual-profile migration, native read-back, resume and rollback.

Never reads live conversation rows or changes the active profile. The two test
profiles are initialized by the installed native CLI; all their chat content is
invented here. Real encrypted image tests are opt-in with --transport.
"""
import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixtures import thread
from codex_migration.common import MigrationError, load, save, readonly, sha, connect
from codex_migration.rpc import RPC, find_cli
from codex_migration.bundle import make_snapshot, seal, mounted
from codex_migration.engine import restore_snapshot, rollback_session, confirm_visual
from codex_migration.history import STATE, HISTORY, version


def initialize(cli, profile, logs):
    profile.mkdir(parents=True)
    rpc = RPC(cli, profile, logs, profile)
    try:
        rpc.call("project/list", {})
        # The native history database is lazy. Seed a metadata-only fixture,
        # then make the native reader initialize its own history schema.
        tid = str(uuid.uuid4())
        rollout = profile / ("rollout-2026-01-01T00-00-00-" + tid + ".jsonl")
        rollout.write_text(json.dumps({"ordinal": 0, "timestamp": "2026-01-01T00:00:00Z", "type": "session_meta", "payload": {"id": tid, "timestamp": "2026-01-01T00:00:00Z", "cwd": str(profile), "originator": "codex_cli_rs", "cli_version": "0.160.0", "source": "cli", "history_mode": "paginated", "base_instructions": {"text": "synthetic fixture"}}}) + "\n")
        with connect(profile / STATE) as d:
            d.execute("insert into threads(id,rollout_path,created_at,updated_at,source,model_provider,cwd,title,sandbox_policy,approval_mode,history_mode) values(?,?,?,?,?,?,?,?,?,?,?)",
                      (tid, str(rollout), 1, 1, "cli", "openai", str(profile), "fixture initializer", '{"type":"read-only"}', "on-request", "paginated"))
        try:
            rpc.call("thread/items/list", {"threadId": tid, "limit": 10, "sortDirection": "asc"})
        except MigrationError:
            print("Synthetic initializer error:", getattr(rpc, "last_error", None))
            raise
        with connect(profile / STATE) as d:
            d.execute("delete from threads where id=?", (tid,))
        with connect(profile / HISTORY) as d:
            for table in ("thread_turns", "thread_items", "thread_history_projection_state", "thread_realtime_items"):
                d.execute("delete from " + table)
        rollout.unlink()
    finally:
        rpc.close()
    with readonly(profile / STATE) as d:
        assert d.execute("select count(*) from threads").fetchone()[0] == 0, "Native init picked up non-fixture chats"
    save(profile / ".codex-global-state.json", {"local-projects": {}, "pinned-thread-ids": ["keep-current-ui-pin"], "keep_sentinel": True})


def run(root, transport=False, runtime=None, empty_target=False, commit_interruption=False, source_version=58, target_version=58, clean=False):
    cli, cli_version = find_cli(target_version)
    assert cli, cli_version
    old_home, new_home = root / "old-home", root / "new-home"
    source, target = old_home / ".codex", new_home / ".codex"
    if source_version == 58 and find_cli(58)[0]:
        initialize(find_cli(58)[0], source, root / "old-init")
    else:
        from fixtures import profile as fixture_profile
        fixture_profile(source, source_version)
    initialize(cli, target, root / "new-init")
    print("Synthetic native schema versions:", version(source / STATE), version(target / STATE), cli_version)
    current = None
    if empty_target:
        for suffix in ("", "-wal", "-shm"):
            (target / (HISTORY + suffix)).unlink(missing_ok=True)
    else:
        current, _ = thread(target, "本机原有历史")
    early, _ = thread(source, "早期消息，中文与 emoji 🌱")
    recent, _ = thread(source, "近期消息", archived=1)
    long_id, long_rollout = thread(source, "长聊天")
    with connect(source / HISTORY) as d:
        for i in range(155):
            item = {"type": "agentMessage", "id": f"long-{i:03d}", "text": f"第 {i} 条，顺序不能变", "phase": None}
            d.execute("insert into thread_items(thread_id,turn_id,item_id,rollout_ordinal,created_at_ms,item_json) values(?,?,?,?,?,?)", (long_id, "turn-1", item["id"], i+3, i+2, json.dumps(item)))
    import zstandard
    compressed = Path(str(long_rollout) + ".zst")
    compressed.write_bytes(zstandard.ZstdCompressor().compress(long_rollout.read_bytes()))
    long_rollout.unlink()
    held = None
    if not clean:
        held, held_file = thread(source, "分支保留待处理")
        value = load(held_file)
        value["payload"]["history_base"] = {"thread_id": "unverified-parent"}
        save(held_file, value)
    project = old_home / "项目 空格"
    project.mkdir()
    (project / "README.md").write_text("synthetic project")
    (project / ".git").mkdir()
    (project / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (project / "dirty.txt").write_text("uncommitted source")
    (project / "conflict.txt").write_text("old candidate")
    import base64
    image = project / "test.png"
    image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aYuoAAAAASUVORK5CYII="))
    with connect(source / HISTORY) as d:
        user = {"type": "userMessage", "id": "local-image-fixture", "content": [{"type": "text", "text": "图片说明", "text_elements": []}, {"type": "localImage", "path": str(image)}]}
        d.execute("insert into thread_items(thread_id,turn_id,item_id,rollout_ordinal,created_at_ms,item_json) values(?,?,?,?,?,?)", (early, "turn-1", user["id"], 3, 3, json.dumps(user)))
    new_project = new_home / "项目 空格"
    new_project.mkdir()
    if not clean:
        (new_project / "conflict.txt").write_text("current stays")
    old_id, old_alias = str(uuid.uuid4()), str(uuid.uuid4())
    with connect(source / STATE) as d:
        d.execute("insert into projects values(?,?,?,?,?,?)", (old_id, "迁移测试项目", "{}", 0, 1, 1))
        d.execute("insert into project_roots values(?,?,?)", (old_id, 0, str(project)))
    save(source / ".codex-global-state.json", {"local-projects": {old_alias: {"id": old_alias, "name": "迁移测试项目", "rootPaths": [str(project)]}},
         "project-order": [old_alias], "pinned-project-ids": [old_alias], "pinned-thread-ids": [early],
         "thread-project-assignments": {early: {"projectKind": "local", "projectId": old_alias}}})
    for folder, script in ((source / "skills/custom-skill", "old-custom"), (target / "skills/custom-skill", "current-custom")):
        if clean and folder.is_relative_to(target):
            continue
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text('---\nname: custom-skill\ndescription: Synthetic customization\n---\nKeep the customized code.\n')
        (folder / "script.py").write_text(script)
    snapshot = root / "snapshot"
    manifest = make_snapshot(source, [{"path": str(project), "name": "迁移测试项目", "project_id": old_id}], snapshot, source_home=old_home)
    storage = root / "sessions"
    # The production wait remains enabled. Bypass it only inside this test, whose
    # two profile roots are private fixture directories and cannot be the live one.
    assert source != Path.home() / ".codex" and target != Path.home() / ".codex"
    with patch("codex_migration.engine.wait_closed"):
        if commit_interruption:
            from codex_migration import engine
            real_save = engine.save
            interrupted = [False]
            def interrupt_after_commit(path, value):
                if Path(path).name == "state.json" and value.get("phase") == "history_committed" and not interrupted[0]:
                    interrupted[0] = True
                    raise RuntimeError("test-power-loss-after-commit")
                return real_save(path, value)
            with patch("codex_migration.engine.save", side_effect=interrupt_after_commit):
                try:
                    restore_snapshot(snapshot, target, storage, home=new_home, authorize_history=True)
                except RuntimeError as e:
                    assert str(e) == "test-power-loss-after-commit"
                else:
                    raise AssertionError("Commit interruption was not exercised")
            assert interrupted[0]
        result = restore_snapshot(snapshot, target, storage, home=new_home, authorize_history=True)
        if not result.get("native_history_restored"):
            print("Synthetic restore issues:", result["issues"])
        assert result["native_history_restored"]
        assert result["native_readback"]["threads"] == 3
        assert result["native_readback"]["items"] == 159
        assert result["native_readback"]["passed"]
        assert len(result["history"]["held"]) == (0 if clean else 1)
        assert any(x.get("reason") == "held_history_branch" for x in result["issues"]) == (not clean)
        assert restore_snapshot(snapshot, target, storage, home=new_home, authorize_history=True) == result
        with readonly(target / STATE) as d:
            assert d.execute("select count(*) from threads").fetchone()[0] == (3 if empty_target else 4)
            if current:
                assert d.execute("select count(*) from threads where id=?", (current,)).fetchone()[0] == 1
            assert d.execute("select count(*) from threads where id=?", (held,)).fetchone()[0] == 0
            assert d.execute("select project_id from threads where id=?", (early,)).fetchone()[0]
        assert (new_project / "conflict.txt").read_text() == ("old candidate" if clean else "current stays")
        assert (new_project / "dirty.txt").read_text() == "uncommitted source"
        sidebar = load(target / ".codex-global-state.json")
        assert sidebar["keep_sentinel"] and "keep-current-ui-pin" in sidebar["pinned-thread-ids"] and early in sidebar["pinned-thread-ids"]
        assert (target / "skills/custom-skill/script.py").read_text() == ("old-custom" if clean else "current-custom")
        if not clean:
            assert list((target / "skills").glob("custom-skill-migrated-*/script.py"))
        if clean:
            assert result["phase"] == "complete", result["issues"]
            assert load(storage / manifest["session_id"] / "result.json")["requires_visual_confirmation"]
            assert confirm_visual(storage / manifest["session_id"])["status"] == "完整通过"
        rollback_session(storage / manifest["session_id"])
        assert not (new_project / "dirty.txt").exists()
        if not clean:
            assert (new_project / "conflict.txt").read_text() == "current stays"
        with readonly(target / STATE) as d:
            assert d.execute("select count(*) from threads").fetchone()[0] == (0 if empty_target else 1)
    rehearsal_proof = load(storage / manifest["session_id"] / "history-plan.json")["proof"]
    assert rehearsal_proof["resume_samples"]
    assert result["attachment_paths_remapped"] == 1
    proof = {"synthetic_only": True, "active_profile_written": False, "cli_version": cli_version,
             "source_schema": source_version, "target_schema": target_version,
             "native_threads_verified": 3, "native_items_verified": 159, "held_branch": (0 if clean else 1),
             "native_resume_samples": len(rehearsal_proof["resume_samples"]), "local_image_path_repaired": True,
             "empty_target_initialized": empty_target, "commit_interruption_recovered": commit_interruption,
             "existing_data_preserved": True, "repeat_idempotent": True, "rollback_verified": True}
    proof["clean_completion_user_confirmation_flow"] = clean
    if transport:
        assert runtime, "Pass the built runtime folder for the self-contained transport test"
        bundle = root / "transport"
        # A generated test password; never a user secret or command-line argument.
        secret = uuid.uuid4().hex
        seal(snapshot, bundle, secret, runtime)
        try:
            with mounted(bundle, "wrong-test-password", root / "mounts"):
                raise AssertionError("Wrong password accepted")
        except MigrationError:
            pass
        with mounted(bundle, secret, root / "mounts") as (point, mounted_manifest):
            assert mounted_manifest["session_id"] == manifest["session_id"]
            assert sha(point / "manifest.json") == sha(snapshot / "manifest.json")
        proof["encrypted_transport_and_wrong_password_verified"] = True
        import os
        import subprocess
        environment = dict(os.environ)
        environment["PATH"] = ""
        verified = subprocess.run([str(Path(runtime).resolve() / "codex-migration"), "verify", "--bundle", str(bundle),
                                   "--storage", str(root / "frozen-storage")], input=secret + "\n", capture_output=True, text=True, env=environment)
        assert verified.returncode == 0, verified.stderr
        assert json.loads(verified.stdout)["verified"]
        proof["frozen_helper_verified_with_empty_path"] = True
        image_file = bundle / "迁移数据.dmg"
        with image_file.open("r+b") as f:
            f.truncate(image_file.stat().st_size - 1)
        try:
            with mounted(bundle, secret, root / "mounts"):
                raise AssertionError("Incomplete transfer accepted")
        except MigrationError:
            pass
        proof["incomplete_transport_rejected"] = True
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", action="store_true")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--empty-target", action="store_true")
    parser.add_argument("--commit-interruption", action="store_true")
    parser.add_argument("--source-version", type=int, choices=[58,59,62], default=58)
    parser.add_argument("--target-version", type=int, choices=[58,59], default=58)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="codex-migration-integration-", dir="/private/tmp") as t:
        try:
            proof = run(Path(t), args.transport, args.runtime, args.empty_target, args.commit_interruption, args.source_version, args.target_version, args.clean)
        except BaseException:
            for p in Path(t).rglob("last-error.json"):
                print("Synthetic API error:", load(p))
            raise
    if args.proof:
        save(args.proof, proof)
    print(json.dumps(proof, ensure_ascii=False, indent=2))
