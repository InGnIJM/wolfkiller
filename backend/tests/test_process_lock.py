from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.persistence.process_lock import ProcessLock, ProcessLockError
import app.persistence.process_lock as process_lock_module


def test_normalized_data_directory_allows_only_one_lock_in_this_process(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    alias = data_dir / ".." / "data"
    first = ProcessLock(data_dir)

    first.acquire()
    try:
        assert first.data_dir == data_dir.resolve()
        with pytest.raises(ProcessLockError, match="already locked"):
            ProcessLock(alias).acquire()
    finally:
        first.release()

    with ProcessLock(alias):
        pass


def test_operating_system_releases_lock_when_owner_process_exits(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    child_code = textwrap.dedent(
        """
        import os
        import sys

        from app.persistence.process_lock import ProcessLock

        lock = ProcessLock(sys.argv[1])
        lock.acquire()
        print("LOCKED", flush=True)
        sys.stdin.read(1)
        os._exit(0)
        """
    )
    backend_dir = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(data_dir)],
        cwd=backend_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "LOCKED"
        with pytest.raises(ProcessLockError, match="already locked"):
            ProcessLock(data_dir).acquire()

        assert process.stdin is not None
        process.stdin.write("x")
        process.stdin.flush()
        assert process.wait(timeout=5) == 0

        with ProcessLock(data_dir):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_lock_state_release_idempotence_and_existing_lock_byte(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / ProcessLock.filename).write_bytes(b"x")
    lock = ProcessLock(data_dir)
    assert lock.locked is False
    lock.acquire()
    assert lock.locked is True
    lock.release()
    assert lock.locked is False
    lock.release()
    assert (data_dir / ProcessLock.filename).read_bytes() == b"x"


@pytest.mark.parametrize("error_number", [None, 12345])
def test_acquire_cleans_up_after_unexpected_errors(
    tmp_path: Path, monkeypatch, error_number: int | None,
) -> None:
    lock = ProcessLock(tmp_path / "data")

    def fail(_stream) -> None:
        if error_number is None:
            raise RuntimeError("boom")
        raise OSError(error_number, "boom")

    monkeypatch.setattr(lock, "_acquire_os_lock", fail)
    expected = RuntimeError if error_number is None else OSError
    with pytest.raises(expected, match="boom"):
        lock.acquire()
    assert lock.locked is False
    with ProcessLock(tmp_path / "data"):
        pass


def test_permission_style_os_error_is_reported_as_lock_conflict(
    tmp_path: Path, monkeypatch,
) -> None:
    lock = ProcessLock(tmp_path / "data")

    def fail(_stream) -> None:
        raise OSError(process_lock_module.errno.EACCES, "denied")

    monkeypatch.setattr(lock, "_acquire_os_lock", fail)
    with pytest.raises(ProcessLockError, match="already locked"):
        lock.acquire()


def test_release_cleans_up_when_os_unlock_raises(tmp_path: Path, monkeypatch) -> None:
    lock = ProcessLock(tmp_path / "data").acquire()

    def fail(_stream) -> None:
        raise OSError("unlock failed")

    monkeypatch.setattr(lock, "_release_os_lock", fail)
    with pytest.raises(OSError, match="unlock failed"):
        lock.release()
    assert lock.locked is False
    with ProcessLock(tmp_path / "data"):
        pass


def test_non_windows_lock_helpers_use_flock(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[int, int]] = []
    fake = SimpleNamespace(
        LOCK_EX=1, LOCK_NB=2, LOCK_UN=4,
        flock=lambda descriptor, flags: calls.append((descriptor, flags)),
    )
    monkeypatch.setattr(process_lock_module.os, "name", "posix")
    monkeypatch.setattr(process_lock_module, "fcntl", fake, raising=False)
    lock_path = tmp_path / "lock"
    with lock_path.open("w+b") as stream:
        ProcessLock._ensure_lock_byte(stream)
        ProcessLock._acquire_os_lock(stream)
        ProcessLock._release_os_lock(stream)
        assert calls == [
            (stream.fileno(), fake.LOCK_EX | fake.LOCK_NB),
            (stream.fileno(), fake.LOCK_UN),
        ]


def test_destructor_swallows_release_failure(tmp_path: Path, monkeypatch) -> None:
    lock = ProcessLock(tmp_path)
    monkeypatch.setattr(lock, "release", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    lock.__del__()


@pytest.mark.parametrize(
    ("failure", "expected"),
    ((OSError(12345, "open failed"), OSError), (KeyboardInterrupt(), KeyboardInterrupt)),
)
def test_acquire_cleans_owned_path_when_open_itself_fails(
    tmp_path: Path, monkeypatch, failure: BaseException, expected: type[BaseException],
) -> None:
    lock = ProcessLock(tmp_path / "data")

    def fail_open(_path, *_args, **_kwargs):
        raise failure

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(expected):
        lock.acquire()
    assert lock._path_key not in process_lock_module._owned_paths


def test_module_imports_posix_lock_backend(monkeypatch) -> None:
    source_path = Path(process_lock_module.__file__)
    source = source_path.read_text(encoding="utf-8")
    fake_fcntl = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "fcntl", fake_fcntl)
    monkeypatch.setattr(process_lock_module.os, "name", "posix")
    namespace = {"__name__": "process_lock_posix_import"}
    exec(compile(source, str(source_path), "exec"), namespace)
    assert namespace["fcntl"] is fake_fcntl
