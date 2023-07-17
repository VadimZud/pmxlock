from os import mkdir, utime, rmdir
from time import time, sleep
from contextlib import AbstractContextManager
from pathlib import Path


class PMXLock(AbstractContextManager):
    pmxlock_dir = Path("/etc/pve/priv/lock")

    def __init__(self, name):
        self.pmxlock_path = self.pmxlock_dir / name

    def mkpmxlock(self):
        try:
            mkdir(self.pmxlock_path)
            return True
        except (FileExistsError, PermissionError):
            return False

    def request_unlock(self):
        try:
            utime(self.pmxlock_path, (0, 0))
        except (PermissionError, FileNotFoundError):
            pass

    def rmpmxlock(self):
        rmdir(self.pmxlock_path)

    def acquire(self, blocking=True, timeout=-1):
        start = time()

        if timeout == 0:
            blocking = False

        self.request_unlock()
        if self.mkpmxlock():
            return True

        if not blocking:
            return False

        while timeout < 0 or time() - start < timeout:
            self.request_unlock()
            if self.mkpmxlock():
                return True
            sleep(1)
        return False

    def update(self):
        utime(self.pmxlock_path, (0, time()))

    def release(self):
        self.rmpmxlock()

    def locked(self):
        can_lock = self.acquire(blocking=False)
        if can_lock:
            self.release()
        return not can_lock

    def __enter__(self):
        self.acquire()
        return super().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return super().__exit__(exc_type, exc_val, exc_tb)
