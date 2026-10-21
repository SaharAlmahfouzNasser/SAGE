# #!/bin/bash
# set -e

# # Kill any existing server on port 8000
# lsof -ti :8000 | xargs kill -9 2>/dev/null || true

# echo "Starting SAGE UI → http://localhost:8000"
# python UI/server.py

#!/bin/bash
set -e

# Kill anything already listening on port 8000 — works without lsof/fuser/ss
# by reading /proc directly and matching the socket inode to its owning PID.
python3 - <<'PYEOF'
import os, signal

port_hex = format(8000, '04X')
target_inode = None
try:
    with open('/proc/net/tcp') as f:
        next(f)
        for line in f:
            parts = line.split()
            local, state, inode = parts[1], parts[3], parts[9]
            ip, p = local.split(':')
            if p == port_hex and state == '0A':  # 0A = LISTEN
                target_inode = inode
                break
except FileNotFoundError:
    pass  # not on Linux / no /proc — nothing we can do here, move on

if target_inode:
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            for fd in os.listdir(f'/proc/{pid}/fd'):
                if os.readlink(f'/proc/{pid}/fd/{fd}') == f'socket:[{target_inode}]':
                    print(f"Killing stale process on port 8000: PID {pid}")
                    os.kill(int(pid), signal.SIGKILL)
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            continue
PYEOF

echo "Starting SAGE UI → http://localhost:8000"
python UI/server.py