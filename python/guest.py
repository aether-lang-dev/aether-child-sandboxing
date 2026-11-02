"""The Python showcase's guest: stock python3, standard library only.

It is started by python/host.ae with one end of a socket at the descriptor
named in AETHER_IPC_FD (3). That socket is its only way out: it answers the
host's questions, and asks the host for the files it wants. Then it tries to
get round the host, and reports what happened.
"""

import ctypes
import os
import socket
import sys

ch = socket.socket(fileno=int(os.environ["AETHER_IPC_FD"]))
pending = b""

# Framing, the same as lib/childbox: the length in bytes in decimal, a
# colon, then the bytes. At most MAX_FRAME bytes a message.
MAX_FRAME = 65536
MAX_HEADER = 6


def send(msg):
    data = msg.encode()
    assert len(data) <= MAX_FRAME, "message over MAX_FRAME"
    ch.sendall(str(len(data)).encode() + b":" + data)


def recv():
    global pending
    while True:
        head, colon, rest = pending.partition(b":")
        if colon:
            if not head.isdigit() or len(head) >= MAX_HEADER or int(head) > MAX_FRAME:
                sys.exit("bad frame from the host")
            size = int(head)
            if len(rest) >= size:
                pending = rest[size:]
                return rest[:size].decode()
        elif len(pending) >= MAX_HEADER:
            sys.exit("bad frame from the host")
        part = ch.recv(4096)
        if not part:
            sys.exit("host went away")
        pending += part


def request(name):
    """Asks the host for a file by name: its text, or None and the reason."""
    send(f"REQUEST {name}")
    reply = recv()
    if reply.startswith("DENY "):
        return None, reply[5:]
    assert reply == f"GRANT {name}", reply
    assert not pending, "unread bytes before a descriptor"
    send(f"TAKE {name}")
    payload, fds, _flags, _addr = socket.recv_fds(ch, 256, 1)
    assert payload.decode() == name and len(fds) == 1
    with os.fdopen(fds[0]) as f:
        return f.read().strip(), ""


def probe_open(path):
    """The guest's own attempt, through libc: LD_PRELOAD's to refuse."""
    try:
        with open(path) as f:
            f.read(1)
        return "ALLOWED"
    except OSError as e:
        return f"refused ({e.strerror})"


def probe_raw_syscall(path):
    """The same through a raw syscall(2), which no LD_PRELOAD sees:
    only a kernel layer (Landlock, seccomp) can refuse this one."""
    if not sys.platform.startswith("linux"):
        return "skipped (Linux only)"
    # openat's number differs by CPU. Only these two are known here.
    SYS_openat = {"x86_64": 257, "aarch64": 56}.get(os.uname().machine)
    if SYS_openat is None:
        return f"skipped (no openat number for {os.uname().machine})"
    AT_FDCWD = -100
    # libc opened by name, not CDLL(None): the global lookup would find the
    # sandbox preload's own syscall() first, and that refuses. This is the
    # call a guest uses to go round the preload entirely.
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    fd = libc.syscall(SYS_openat, AT_FDCWD, path.encode(), os.O_RDONLY)
    if fd >= 0:
        os.close(fd)
        return "ALLOWED"
    return f"refused ({os.strerror(ctypes.get_errno())})"


def probe_connect():
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=2).close()
        return "ALLOWED"
    except OSError as e:
        return f"refused ({e.strerror or e})"


while True:
    msg = recv()
    verb, _, rest = msg.partition(" ")
    if verb == "HELLO":
        send(f"HELLO from Python {sys.version.split()[0]}")
    elif verb == "ASK":
        op, a, b = rest.split()
        send(f"ANSWER {int(a) * int(b)}" if op == "multiply" else f"ANSWER ? {op}")

        # The guest's own turn: it asks the host for things.
        text, why = request("config")
        send(f"SAY config says: {text}" if text is not None else f"SAY no config: {why}")
        text, why = request("/etc/passwd")
        send("SAY read /etc/passwd through the host" if text is not None
             else f"SAY the host said no: {why}")

        # And it tries to help itself.
        send(f"PROBE open('/etc/passwd'): {probe_open('/etc/passwd')}")
        send(f"PROBE raw syscall openat('/etc/passwd'): {probe_raw_syscall('/etc/passwd')}")
        send(f"PROBE connect 1.1.1.1:443: {probe_connect()}")
        send("DONE")
    elif verb == "BYE":
        send("BYE")
        break
