import os, fcntl
from time import time, sleep
from contextlib import AbstractContextManager
from abc import abstractmethod
from pathlib import Path


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
        self.fd = os.open(self.path, os.O_RDONLY | os.O_CREAT)
        try:
            acquire_res = super().acquire(blocking, timeout)

            if acquire_res:
                self.locked_fd = self.fd
            return acquire_res
        except Exception:
            os.close(self.fd)
            raise

    def release(self):
        os.close(self.locked_fd)
        self.locked_fd = None


class PMXRecoverableLock(PMXLock):
    def acquire(self, blocking=True, timeout=-1):
        try:
            self.update()
            return True
        except (PermissionError, FileNotFoundError):
            return super().acquire(blocking, timeout)


class LocksChain(LockBase):
    def __init__(self, *locks):
        self.locks = locks

    @staticmethod
    def timeouts(timeout):
        start = time()

        while True:
            if timeout <= 0:
                yield timeout
                continue

            current_timeout = timeout - (time() - start)
            if current_timeout <= 0:
                timeout = current_timeout = 0
            yield current_timeout

    @staticmethod
    def release_locks(locks):
        for lock in reversed(locks):
            lock.release()

    def acquire(self, blocking=True, timeout=-1):
        timeouts = self.timeouts(timeout)
        acquired = []
        try:
            for lock, timeout in zip(self.locks, timeouts):
                if not lock.acquire(blocking, timeout):
                    self.release_locks(acquired)
                    return False
                acquired.append(lock)
            return True
        except Exception:
            self.release_locks(acquired)
            raise

    def release(self):
        self.release_locks(self.locks)


class ClusterLock(LocksChain):
    flock_dir = Path("/run/lock/pmxlock")
    pmxlock_dir = Path("/etc/pve/priv/lock")

    def __init__(self, name):
        self.flock_dir.mkdir(exist_ok=True)
        self.flock = FLock(self.flock_dir / name)
        self.pmxlock = PMXRecoverableLock(self.pmxlock_dir / name)
        super().__init__(self.flock, self.pmxlock)

    def update(self):
        self.pmxlock.update()
