# pmxlock
Python classes and CLI tools for cluster-wide Proxmox pmxcfs locks.

## Installation
Use `pip` for install:

```bash
pip install pmxlock
```

## CLI usage
Start simple command:

```bash
pmxlock mylockname echo hello
```

Only one command with `mylockname` lock can run in current time in cluster.
Other commands will be blocked until current command completes.

You can exit with failure code instead of blocking.
Use `-n` flag for nonblocking mode.
Failure exit code is `1` by default. Use `-E` flag for other failure code:

```bash
pmxlock -n -E2 mylockname echo hello
```

Command above exits with code `2` if `mylockname` already acquired.

You can use timeout mode also:

```bash
pmxlock -w10 mylockname echo hello
```

Command above waits no more than 10 seconds and exits with code `1` if
`mylockname` cannot be acquired.

If command has options use `--` before command in order stop options parsing by
`pmxlock`:

```bash
pmxlock mylockname -- echo -n hello
```

For `mylockname`-lock `pmxlock` uses node-wide `/run/lock/pmxlock/mylockname`
flock and cluster-wide `/etc/pve/priv/lock/mylockname` pmxcfs-lock. If `pmxlock`
terminated abnormaly (e.g. SIGKILL) `/etc/pve/priv/lock/mylockname` pmxcfs-lock
will remain locked for pmxcfs timeout period (currently 120 seconds hardcoded
in Proxmox). Other process on the same node can acquire orphaned lock
without waiting. You can use `pmxlock-gc` cli command for clean all orphaned
locks on current node.

```bash
pmxlock-gc
```

## Class usage
Cluster-wide lock implemented by `ClusterLock` class:

```python
from pmxlock import ClusterLock

lock = ClusterLock("mylockname")
```

Short operation in blocking mode:

```python
lock.acquire()
try:
    # Your operation should be short or timeouted.
    # Hardcoded proxmox timeout == 120 seconds.
    # Take overheads into account and use shorter timeout 
    do_work(timeout=0.8 * 120) 
finally:
    lock.release()
```

`ClusterLock` lock implements context manager protocol for blocking mode:

```python
with ClusterLock("mylockname"):
    do_work(timeout=0.8 * 120)
```

For long operations you need to use `lock.update()` regularly.
You can use subprocess for it:

```python
import subprocess

with ClusterLock("mylockname") as lock:
    proc = subprocess.Popen(command)
    while True:
        try:
            proc.wait(0.8 * 120)
            break
        except subprocess.TimeoutExpired:
            lock.update()
```

You can start operation in nonblocking mode:

```python
if lock.acquire(blocking=False):
    try:
        do_work()
    finally:
        lock.release()
else:
    cannot_acquire_lock()
```

And timeout mode:

```python
if lock.acquire(timeout=10):
    try:
        do_work()
    finally:
        lock.release()
else:
    cannot_acquire_lock()
```

