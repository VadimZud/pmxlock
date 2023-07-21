import sys
from pathlib import Path
from .. import ClusterLock


def main():
    flock_dir = Path("/run/lock/pmxlock")

    for flock in flock_dir.iterdir():
        lock = ClusterLock(flock.name)
        if lock.acquire(blocking=False):
            lock.release()

    return 0


if __name__ == "__main__":
    sys.exit(main())
