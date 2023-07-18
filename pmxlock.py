import os, fcntl
from time import time, sleep
from contextlib import AbstractContextManager
from abc import abstractmethod


class LockBase(AbstractContextManager):
    def acquire_nonblocking(self):
        raise NotImplementedError

    def acquire_blocking(self):
        while not self.acquire_nonblocking():
            sleep(1)
        return True

    def acquire_timeout(self, timeout):
        start = time()
        while not self.acquire_nonblocking():
            if time() - start > timeout:
                return False
            sleep(1)
        return True

    def acquire(self, blocking=True, timeout=-1):
        if timeout == 0:
            blocking = False

        if blocking:
            if timeout > 0:
                return self.acquire_timeout(timeout)
            else:
                return self.acquire_blocking()
        else:
            return self.acquire_nonblocking()

    @abstractmethod
    def release(self):
        pass

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


class PMXLock(LockBase):
    def __init__(self, path):
        self.path = path

    def mklock(self):
        try:
            os.mkdir(self.path)
            return True
        except (FileExistsError, PermissionError):
            return False

    def request_unlock(self):
        try:
            os.utime(self.path, (0, 0))
        except (PermissionError, FileNotFoundError):
            pass

    def acquire_nonblocking(self):
        self.request_unlock()
        return self.mklock()

    def release(self):
        os.rmdir(self.path)

    def update(self):
        os.utime(self.path, (0, time()))


class FLock(LockBase):
    def __init__(self, path):
        self.path = path
        self.locked_fd = None

    def acquire_nonblocking(self):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def acquire_blocking(self):
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return True

    def acquire(self, blocking=True, timeout=-1):
        try:
            self.fd = None
            self.fd = os.open(self.path, os.O_RDONLY | os.O_CREAT)

            acquire_res = super().acquire(blocking, timeout)

            if acquire_res:
                self.locked_fd = self.fd
            return acquire_res
        except Exception:
            if self.fd is not None:
                os.close(self.fd)
            raise

    def release(self):
        os.close(self.locked_fd)
        self.locked_fd = None
