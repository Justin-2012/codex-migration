"""Build a self-contained arm64 release. Run in an isolated build venv."""
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("Build and verify the macOS arm64 release on a Mac arm64 host")
    out = ROOT / "dist"
    work = ROOT / "build"
    work.mkdir(exist_ok=True)
    template = work / "skill-template.json"
    source = ROOT / "skill/codex-migration"
    template.write_text(json.dumps({str(p.relative_to(source)): p.read_text() for p in source.rglob("*") if p.is_file()}, ensure_ascii=False, indent=2) + "\n")
    runtime = out / "codex-migration"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
                    "--name", "codex-migration", "--paths", str(ROOT / "src"),
                    "--distpath", str(out), "--workpath", str(work / "pyinstaller"), "--specpath", str(work),
                    "--add-data", str(ROOT / "src/codex_migration/assets") + ":codex_migration/assets",
                    "--add-data", str(template) + ":codex_migration/assets",
                    "--collect-all", "zstandard", "--collect-all", "yaml", str(ROOT / "tools/entry.py")], check=True)
    release = out / "Codex迁移接力-macOS-arm64"
    if release.exists():
        shutil.rmtree(release)
    release.mkdir()
    shutil.copytree(runtime, release / "_runtime", symlinks=True)
    shutil.copytree(ROOT / "skill", release / "skill")
    shutil.copy2(ROOT / "README.md", release / "使用说明.md")
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md", "llms.txt"):
        shutil.copy2(ROOT / name, release / name)
    shutil.copytree(ROOT / "docs", release / "docs")
    licenses = release / "licenses"
    licenses.mkdir()
    shutil.copytree(ROOT / "licenses", licenses, dirs_exist_ok=True)
    python_license = Path(sys.base_prefix) / ("lib/python" + str(sys.version_info.major) + "." + str(sys.version_info.minor)) / "LICENSE.txt"
    if not python_license.is_file():
        raise SystemExit("Python license is required for redistribution")
    shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
    for package in ("pyinstaller", "zstandard", "PyYAML"):
        distribution = importlib.metadata.distribution(package)
        notices = [p for p in distribution.files if "/licenses/" in str(p)]
        if not notices:
            raise SystemExit("Missing dependency license: " + package)
        for notice in notices:
            shutil.copy2(distribution.locate_file(notice), licenses / (package + "-" + Path(str(notice)).name))
    launcher = release / "安装迁移Skill.command"
    launcher.write_text('#!/bin/zsh\nset -eu\ntask_tool_dir=${0:A:h}\ncd "$task_tool_dir"\nprint "正在核验安装包…"\n'
                        'if [[ ! -f "工具校验.sha256" ]] || [[ $(/usr/bin/wc -l < "工具校验.sha256") -ne EXPECTED_COUNT ]] || ! /usr/bin/shasum -a 256 -c "工具校验.sha256" >/dev/null 2>&1; then\n'
                        '  print "安装包尚未完整下载或已变化，请重新下载完整发行包。"\n  read "task_wait?按回车关闭窗口："\n  exit 1\nfi\n'
                        '"$task_tool_dir/_runtime/codex-migration" install\nread "task_done?安装完成后按回车关闭窗口："\n')
    launcher.chmod(0o755)
    included = [p for p in sorted(release.rglob("*")) if p.is_file()]
    launcher.write_text(launcher.read_text().replace("EXPECTED_COUNT", str(len(included))))
    files = []
    for p in included:
        if p.is_file() and not p.is_symlink():
            files.append({"path": str(p.relative_to(release)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    (release / "tool-checksums.json").write_text(json.dumps({"version": "0.1.0", "architecture": "arm64", "files": files}, indent=2) + "\n")
    (release / "工具校验.sha256").write_text("\n".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + str(p.relative_to(release)) for p in included) + "\n")
    zipfile = out / "codex-migration-v0.1.0-macos-arm64.zip"
    zipfile.unlink(missing_ok=True)
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(release), str(zipfile)], check=True)
    print(zipfile)


if __name__ == "__main__":
    main()
