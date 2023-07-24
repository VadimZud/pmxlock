"""Proxmox cluster-wide locks and related classes."""

import os
import fcntl
import time
import contextlib
import pathlib
from abc import abstractmethod
from collections.abc import Generator


class LockBase(contextlib.AbstractContextManager):
    """Abstract base class for `threading.Lock`-like lock

    Subclass must implement `acquire()` or `acquire_nonblocking()` method.
    Subclass must implement `release()` method.
    Base class implements `locked()` method and context manager protocol.
    """

    def acquire_nonblocking(self) -> bool:
        """Acquire lock in nonblocking mode.

        Default `acquire()` method with the assistance `acquire_blocking()` and
        `acquire_timeout()` methods implements valid blocking/timeout logic.
        Subclass can ignore this method and override `acquire()` method directy.
        
        Returns:
            Acquire status.
        """
        raise NotImplementedError

    def acquire_blocking(self) -> bool:
        """Acquire lock in blocking mode.
        
        If base lock mechanism provides blocking mode, subclass can use it here.
        Default implementation uses `acquire_nonblocking()` method.
        
        Returns:
            Acquire status (usually, it is always true).
        """
        while not self.acquire_nonblocking():
            time.sleep(1)
        return True

    def acquire_timeout(self, timeout: float) -> bool:
        """Acquire lock in blocking mode with timeout.
        
        If base lock mechanism provides blocking mode with timeout, subclass can
        use it here.
        Default implementation uses `acquire_nonblocking()` method.

        Args:
            timeout: max seconds for lock waiting.
        
        Returns:
            Acquire status.
        """
        start = time.time()
        while not self.acquire_nonblocking():
            if time.time() - start > timeout:
                return False
            time.sleep(1)
        return True

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquire lock.
        
        Default implementation uses `acquire_nonblocking()`,
        `acquire_blocking()` and `acquire_timeout()` methods.
        
        Args:
            blocking: use blocking or nonblocking mode.
            timeout: if blocking == True, max seconds for lock waiting.
        
        Returns:
            Acquire status.
        """
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
    def release(self) -> None:
        """Release lock."""
        pass

    def locked(self) -> bool:
        """Return current lock status."""
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
    """Proxmox pmxcfs lock.
    
    See https://github.com/proxmox/pve-cluster/blob/master/src/README for lock
    mechanism description."""

    def __init__(self, path: str):
        """Initialize the instance based on lock directory path.

        Args:
            path: lock directory in pmxcfs file system.
        """
        self.path = path

    def mklock(self) -> bool:
        """Try create lock directory.
        
        Returns:
            `True` if lock directory created, else `False`.
        """
        try:
            os.mkdir(self.path)
            return True
        except (FileExistsError, PermissionError):
            return False

    def request_unlock(self) -> None:
        """Request pmxcfs to remove expired locks."""
        try:
            os.utime(self.path, (0, 0))
        except (PermissionError, FileNotFoundError):
            pass

    def acquire_nonblocking(self) -> bool:
        self.request_unlock()
        return self.mklock()

    def release(self) -> None:
        os.rmdir(self.path)

    def update(self) -> None:
        """Renew lock owning.
        
        pmxcfs lock expire on timeout (120 seconds hardcoded in Proxmox).
        Client code can renew lock owning if needed.
        """
        os.utime(self.path, (0, time.time()))


class FLock(LockBase):
    """flock(2) based lock."""

    def __init__(self, path: str):
        """Initialize the instance based on lock file path.

        Args:
            path: lock file path.
        """
        self.path = path
        self.locked_fd = None

    def acquire_nonblocking(self) -> bool:
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def acquire_blocking(self) -> bool:
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return True

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        self.fd = os.open(self.path, os.O_RDONLY | os.O_CREAT)
        try:
            acquire_res = super().acquire(blocking, timeout)

            if acquire_res:
                self.locked_fd = self.fd
            return acquire_res
        except Exception:
            os.close(self.fd)
            raise

    def release(self) -> None:
        os.close(self.locked_fd)
        self.locked_fd = None


class PMXRecoverableLock(PMXLock):
    """Recoverable Proxmox pmxcfs lock.
    
    If the process holding the `PMXLock` terminates abnormally (e.g. SIGKILL),
    other processes may hang for 120 seconds waiting expire lock timeout.
    `PMXRecoverableLock` can be acquired by another process on the same node
    without waiting.
    
    Note:
        Use an additional node-wide lock before `PMXRecoverableLock`."""

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        try:
            self.update()
            return True
        except (PermissionError, FileNotFoundError):
            return super().acquire(blocking, timeout)


def timeouts(timeout: float) -> Generator[float, None, None]:
    """Yield the time remaining before the timeout expires.

    Starts counting at the moment of the first call.
    
    Args:
        timeout: start timeout in seconds
    
    Yields:
        If `timeout` == -1 or `timeout` == 0 then yields timeout indefinitely.
        If `timeout` > 0 yields remaining time at the moment of the current
        call. If the time is over return 0 indefinitely.
    """
    start = time.time()

    while True:
        if timeout <= 0:
            yield timeout
            continue

        current_timeout = timeout - (time.time() - start)
        if current_timeout <= 0:
            timeout = current_timeout = 0
        yield current_timeout


class LocksChain(LockBase):
    """Locks sequence, acquired in "all or nothing" mode"""

    def __init__(self, *locks: LockBase):
        """Initialize the instance based on multiple locks.

        Args:
            *locks: locks in acquire order.
        """
        self.locks = locks
        self.acquired: list[LockBase] = []

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        try:
            for lock, t in zip(self.locks, timeouts(timeout)):
                if not lock.acquire(blocking, t):
                    self.release()
                    return False
                self.acquired.append(lock)
            return True
        except Exception:
            self.release()
            raise

    def release(self) -> None:
        for lock in reversed(self.acquired):
            lock.release()
        self.acquired = []


class ClusterLock(LocksChain):
    """Cluster-wide Proxmox lock.
    
    Lock has name. Creates lock file /run/lock/pmxlock/name and 
    lock directory /etc/pve/priv/lock/name.
    """

    flock_dir = pathlib.Path("/run/lock/pmxlock")
    pmxlock_dir = pathlib.Path("/etc/pve/priv/lock")

    def __init__(self, name: str):
        """Initialize the instance based on name.

        Args:
            *locks: locks in acquire order.
        """
        self.flock_dir.mkdir(exist_ok=True)
        self.flock = FLock(self.flock_dir / name)
        self.pmxlock = PMXRecoverableLock(self.pmxlock_dir / name)
        super().__init__(self.flock, self.pmxlock)

    def update(self) -> None:
        """Renew lock owning.
        
        pmxcfs lock expire on timeout (120 seconds hardcoded in Proxmox).
        Client code can renew lock owning if needed.
        """
        self.pmxlock.update()
