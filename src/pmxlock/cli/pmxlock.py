import argparse
import sys
import subprocess
import shutil
from .. import ClusterLock

PROXMOX_LOCK_EXPIRE_TIMEOUT = 120
PROXMOX_LOCK_UPDATE_INTERVAL = PROXMOX_LOCK_EXPIRE_TIMEOUT * 0.8


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-n",
        "--nb",
        "--nonblock",
        dest="blocking",
        action="store_false",
        help="fail rather than wait if the lock cannot be immediately acquired",
    )
    parser.add_argument(
        "-w",
        "--wait",
        "--timeout",
        dest="timeout",
        type=float,
        default=-1,
        metavar="seconds",
        help="fail if the lock cannot be acquired within seconds",
    )
    parser.add_argument(
        "-E",
        "--conflict-exit-code",
        type=int,
        default=1,
        metavar="number",
        help="the exit status used when the -n option is in use, and the conflicting lock exists, or the -w option is in use, and the timeout is reached",
    )
    parser.add_argument("lock_name", help="name of lock")
    parser.add_argument("command", help="command")
    parser.add_argument("args", nargs="*", help="command args")
    args = parser.parse_args()

    lock = ClusterLock(args.lock_name)
    if lock.acquire(blocking=args.blocking, timeout=args.timeout):
        try:
            proc = subprocess.Popen([shutil.which(args.command), *args.args])
            while True:
                try:
                    return proc.wait(PROXMOX_LOCK_UPDATE_INTERVAL)
                except subprocess.TimeoutExpired:
                    lock.update()
        finally:
            lock.release()
    else:
        return args.conflict_exit_code


if __name__ == "__main__":
    sys.exit(main())
