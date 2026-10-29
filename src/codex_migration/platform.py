import os
import platform
import plistlib
import subprocess
import time
from pathlib import Path
from .common import MigrationError


def default_profile():
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()


def default_storage():
    return Path.home() / "Library/Application Support/CodexMigration"


def check_platform():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise MigrationError("首版助手仅在 macOS Apple Silicon 验证；本机不执行原生迁移")


def clients_running():
    r = subprocess.run(["/bin/ps", "-axo", "pid=,comm="], capture_output=True, text=True)
    if r.returncode:
        raise MigrationError("无法核实客户端是否退出，未开始修改数据")
    own = os.getpid()
    for line in r.stdout.splitlines():
        row = line.strip().split(None, 1)
        if len(row) != 2 or int(row[0]) == own:
            continue
        name = Path(row[1]).name.lower()
        if name in {"chatgpt", "codex", "codex-app-server"}:
            return True
    return False


def wait_closed(wait=False, timeout=900):
    if not clients_running():
        return
    if not wait:
        raise MigrationError("请先完全退出 Codex / ChatGPT；助手不会强制结束客户端")
    print("请按 Cmd+Q 完全退出 Codex / ChatGPT。本窗口会自动继续，请保持开启。", flush=True)
    deadline = time.monotonic() + timeout
    while clients_running():
        if time.monotonic() >= deadline:
            raise MigrationError("等待退出已超时；没有开始恢复，可稍后继续")
        time.sleep(1)
    time.sleep(2)


def app_info():
    for name in ("ChatGPT", "Codex"):
        app = Path("/Applications") / (name + ".app")
        p = app / "Contents/Info.plist"
        if p.is_file():
            with p.open("rb") as f:
                data = plistlib.load(f)
            return {"path": str(app), "version": data.get("CFBundleShortVersionString"), "build": data.get("CFBundleVersion")}
    return None


def reopen_app():
    info = app_info()
    if info:
        subprocess.run(["/usr/bin/open", info["path"]], check=False)
