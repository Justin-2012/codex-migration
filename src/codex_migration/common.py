import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path


class MigrationError(RuntimeError):
    pass


def load(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".migration-", delete=False) as f:
        temp = Path(f.name)
        os.chmod(temp, 0o600)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    fsync_dir(path.parent)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def safe_child(root, relative):
    root, rel = Path(root).absolute(), Path(relative)
    if rel.is_absolute() or not rel.parts or any(p in ("..", "") for p in rel.parts):
        raise MigrationError("清单中有不安全的文件路径")
    target = root / rel
    no_symlink_parents(target)
    if target.is_symlink():
        raise MigrationError("拒绝通过符号链接读写迁移文件")
    return target


def no_symlink_parents(path):
    # /tmp and /var are system aliases; normalize them at the selected root.
    for p in [Path(path).parent, *Path(path).parents]:
        if p.is_symlink() and str(p) not in ("/tmp", "/var"):
            raise MigrationError("目标目录包含符号链接，请选择实际目录")


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path, **kwargs):
    return sqlite3.connect(path, factory=ClosingConnection, **kwargs)


def readonly(path):
    d = connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    d.row_factory = sqlite3.Row
    return d


def backup_db(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with readonly(source) as a, sqlite3.connect(target) as z:
        a.backup(z)
    a.close()
    z.close()
    with connect(target) as z:
        z.execute("pragma wal_checkpoint(truncate)")
        z.execute("pragma journal_mode=DELETE")
        if z.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise MigrationError("备份数据库完整性检查失败")


@contextlib.contextmanager
def lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MigrationError("已有迁移助手运行，请回到原进度窗口")
        yield


def require_space(path, needed):
    p = Path(path)
    while not p.exists():
        p = p.parent
    if shutil.disk_usage(p).free < needed:
        raise MigrationError("磁盘空间不足，尚未开始恢复；请释放空间后继续")
