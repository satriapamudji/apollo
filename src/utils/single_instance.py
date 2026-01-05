"""Cross-platform single-instance lock for the trading bot."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TextIO


class SingleInstanceAlreadyRunning(RuntimeError):
    def __init__(self, lock_path: str, pid: int | None) -> None:
        self.lock_path = lock_path
        self.pid = pid
        pid_hint = f" (pid={pid})" if pid else ""
        super().__init__(f"Another instance is already running{pid_hint}: {lock_path}")


class SingleInstanceLock:
    """Acquire an exclusive lock on a file (released automatically on process exit)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._fh: TextIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        fh.seek(0)
        existing_pid = _read_pid(fh)
        fh.seek(0)
        try:
            _lock_file(fh)
        except OSError as exc:
            fh.close()
            raise SingleInstanceAlreadyRunning(str(self.path), existing_pid) from exc

        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        fh = self._fh
        if not fh:
            return
        try:
            _unlock_file(fh)
        except OSError:
            pass
        fh.close()
        self._fh = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.release()


def _read_pid(fh: TextIO) -> int | None:
    try:
        content = fh.read().strip()
    except OSError:
        return None
    if not content:
        return None
    token = content.split()[0]
    try:
        pid = int(token)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _lock_file(fh: TextIO) -> None:
    if os.name == "nt":
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fh: TextIO) -> None:
    if os.name == "nt":
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

