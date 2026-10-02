"""Only one run at a time (pick.py, auto.py, daily.py, upload.py all control the
same game, OBS and history).

A run takes a Windows file lock on run.lock in the project folder. A second run
started meanwhile can't get it and exits without changing anything. The operating
system releases the lock when the process ends, also after a crash or power cut,
so a lock can never get stuck. The file itself says which process holds it.
"""
import msvcrt
import os
from datetime import datetime
from pathlib import Path

LOCK_OFFSET = 4096  # the locked byte: past the text, so other runs can still read who holds it


class RunLock:
    def __init__(self, path: Path):
        self.path = path
        self.f = None
        self.holder = ""  # who holds the lock, when it could not be taken

    def acquire(self) -> bool:
        f = open(self.path, "a+", encoding="utf-8")
        try:
            f.seek(LOCK_OFFSET)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            f.seek(0)
            self.holder = f.read().strip() or "another run"
            f.close()
            return False
        f.seek(0)
        f.truncate()
        f.write(f"process {os.getpid()}, started {datetime.now():%Y-%m-%d %H:%M}\n")
        f.flush()
        self.f = f
        return True

    def release(self) -> None:
        if self.f:
            try:
                self.f.seek(LOCK_OFFSET)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self.f.close()
            self.f = None

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
