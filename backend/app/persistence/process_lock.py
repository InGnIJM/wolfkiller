"""Cross-platform, process-scoped lock for a WolfKiller data directory."""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class ProcessLockError(RuntimeError):
    """Raised when a data directory is already owned by another lock."""


_owned_paths: set[str] = set()
_owned_paths_guard = threading.Lock()


class ProcessLock:
    """Hold an exclusive OS lock for one normalized data directory."""

    filename = ".wolfkiller.lock"

    def __init__(self, data_dir: str | os.PathLike[str]) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve(strict=False)
        self.lock_path = self.data_dir / self.filename
        self._path_key = os.path.normcase(os.fspath(self.data_dir))
        self._file: BinaryIO | None = None

    @property
    def locked(self) -> bool:
        return self._file is not None

    def acquire(self) -> ProcessLock:
        """Acquire immediately, failing when this data directory is in use."""
        with _owned_paths_guard:
            if self._file is not None or self._path_key in _owned_paths:
                raise self._already_locked()
            _owned_paths.add(self._path_key)

        lock_file: BinaryIO | None = None
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            lock_file = self.lock_path.open("a+b")
            self._ensure_lock_byte(lock_file)
            self._acquire_os_lock(lock_file)
            self._file = lock_file
            return self
        except OSError as error:
            if lock_file is not None:
                lock_file.close()
            self._forget_owned_path()
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise self._already_locked() from error
            raise
        except BaseException:
            if lock_file is not None:
                lock_file.close()
            self._forget_owned_path()
            raise

    def release(self) -> None:
        """Release the OS lock. Releasing an unlocked instance is harmless."""
        lock_file = self._file
        if lock_file is None:
            return
        self._file = None
        try:
            self._release_os_lock(lock_file)
        finally:
            lock_file.close()
            self._forget_owned_path()

    def __enter__(self) -> ProcessLock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass

    def _already_locked(self) -> ProcessLockError:
        return ProcessLockError(f"data directory is already locked: {self.data_dir}")

    def _forget_owned_path(self) -> None:
        with _owned_paths_guard:
            _owned_paths.discard(self._path_key)

    @staticmethod
    def _ensure_lock_byte(lock_file: BinaryIO) -> None:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)

    @staticmethod
    def _acquire_os_lock(lock_file: BinaryIO) -> None:
        if os.name == "nt":
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _release_os_lock(lock_file: BinaryIO) -> None:
        lock_file.seek(0)
        if os.name == "nt":
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
