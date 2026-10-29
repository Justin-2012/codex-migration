"""Local app-server methods only; there is intentionally no turn/start helper."""
import json
import os
import selectors
import shutil
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path
from .common import MigrationError, save


def find_cli(target_version=None):
    candidates = [shutil.which("codex"), "/opt/homebrew/bin/codex", "/usr/local/bin/codex"]
    for app in ("ChatGPT", "Codex"):
        candidates.extend([f"/Applications/{app}.app/Contents/Resources/codex-cli/bin/codex",
                           f"/Applications/{app}.app/Contents/Resources/codex"])
    if target_version == 59:
        candidates = [c for c in candidates if c and c.startswith("/Applications/")] + candidates
    for item in dict.fromkeys(c for c in candidates if c):
        p = Path(item)
        if not p.is_file():
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="codex-migration-version-") as task_probe_dir:
                environment = dict(os.environ)
                environment["CODEX_HOME"] = task_probe_dir
                v = subprocess.run([str(p), "--version"], capture_output=True, text=True, timeout=10, check=True, env=environment).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        allowed = [" 0.160.0"] if target_version == 58 else [" 0.160.0", " 0.162.0-alpha.17.2"]
        if any(v.endswith(suffix) for suffix in allowed):
            return str(p), v
    return None, "未发现适配器已验证的 Codex CLI；仅提供文件与参考资料恢复"


class RPC:
    def __init__(self, cli, profile, logs, config_profile=None):
        logs = Path(logs)
        logs.mkdir(parents=True, exist_ok=True)
        self.logs = logs
        args = [str(cli), "-c", "sqlite_home=" + json.dumps(str(profile)), "-c", "log_dir=" + json.dumps(str(logs)),
                "-c", "sandbox_mode=\"read-only\"", "-c", "approval_policy=\"on-request\"", "-c", "analytics.enabled=false"]
        configs = {Path(config_profile or profile) / "config.toml"}
        names = set()
        for config in configs:
            if config.is_file():
                names.update(tomllib.loads(config.read_text()).get("mcp_servers", {}))
        for name in sorted(names):
                # TOML dotted keys: quote a non-bare component correctly.
            key = name if all(c.isalnum() or c in "_-" for c in name) else json.dumps(name)
            args.extend(["-c", f"mcp_servers.{key}.enabled=false"])
        args.extend(["app-server", "--stdio"])
        self.log = (logs / "app-server.log").open("ab")
        # This is the documented Codex profile selector, scoped to this child
        # process only; never change the caller's environment or use HOME as a
        # scratch variable. sqlite_home alone still backfills the default sessions.
        environment = dict(os.environ)
        environment["CODEX_HOME"] = str(Path(config_profile or profile).resolve())
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, bufsize=0, env=environment)
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.p.stdout, selectors.EVENT_READ)
        self.buf, self.n = b"", 0
        try:
            self.call("initialize", {"clientInfo": {"name": "codex_migration", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise

    def send(self, value):
        self.p.stdin.write((json.dumps(value) + "\n").encode())
        self.p.stdin.flush()

    def call(self, method, params):
        if method not in {"initialize", "project/list", "project/create", "thread/items/list", "thread/turns/list", "thread/resume", "skills/list"}:
            raise MigrationError("迁移助手拒绝执行非迁移协议方法")
        self.n += 1
        self.send({"id": self.n, "method": method, "params": params})
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if b"\n" not in self.buf:
                if not self.sel.select(max(0, deadline - time.monotonic())):
                    break
                data = os.read(self.p.stdout.fileno(), 1024 * 1024)
                if not data:
                    raise MigrationError("原生验证进程已退出，未把验证标为通过")
                self.buf += data
                continue
            line, self.buf = self.buf.split(b"\n", 1)
            if not line.strip():
                continue
            value = json.loads(line)
            if value.get("id") == self.n:
                if "error" in value:
                    self.last_error = value["error"]
                    save(self.logs / "last-error.json", {"method": method, "error": value["error"]})
                    raise MigrationError("原生接口验证未通过；详细记录保留在本机私有目录")
                return value["result"]
        raise MigrationError("原生验证超时，保留进度供继续")

    def close(self):
        if self.p.poll() is None:
            self.p.terminate()
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        self.sel.close()
        self.log.close()
