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

# Framing, std.ipc's: a 4-byte big-endian length, then the bytes. At most
# MAX_FRAME bytes a message. Reads take exactly a frame and never a byte
# more, so a descriptor riding on the byte after a GRANT frame is left for
# recv_fds.
MAX_FRAME = 65536


def send(msg):
    data = msg.encode()
    assert len(data) <= MAX_FRAME, "message over MAX_FRAME"
    ch.sendall(len(data).to_bytes(4, "big") + data)


def read_exact(n):
    got = b""
    while len(got) < n:
        part = ch.recv(n - len(got))
        if not part:
            sys.exit("host went away")
        got += part
    return got


def recv():
    size = int.from_bytes(read_exact(4), "big")
    if size > MAX_FRAME:
        sys.exit("frame from the host over MAX_FRAME")
    return read_exact(size).decode()


def request(name):
    """Asks the host for a file by name: its text, or None and the reason."""
    send(f"REQUEST {name}")
    reply = recv()
    if reply.startswith("DENY "):
        return None, reply[5:]
    assert reply == f"GRANT {name}", reply
    # The descriptor rides on the next byte: one recvmsg takes both.
    payload, fds, _flags, _addr = socket.recv_fds(ch, 1, 1)
    assert payload == b"F" and len(fds) == 1
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


class DlInfo(ctypes.Structure):
    _fields_ = [("dli_fname", ctypes.c_char_p), ("dli_fbase", ctypes.c_void_p),
                ("dli_sname", ctypes.c_char_p), ("dli_saddr", ctypes.c_void_p)]


def libc_file():
    """libc's path and load address: dladdr on its strlen. CDLL(None) is the
    program's own handle, which the preload lets through."""
    me = ctypes.CDLL(None)
    info = DlInfo()
    if not me.dladdr(ctypes.cast(me.strlen, ctypes.c_void_p), ctypes.byref(info)):
        return None, None
    return (info.dli_fname or b"").decode(), info.dli_fbase


def probe_dlopen_libc():
    """A handle on libc by its absolute path, the usual way round the
    preload: dlsym on it finds the real open(). The preload refuses libc by
    any name or path."""
    if not sys.platform.startswith("linux"):
        return "skipped (Linux only)"
    path, _ = libc_file()
    if not path or not path.startswith("/"):
        return "skipped (libc's path not found)"
    try:
        ctypes.CDLL(path)
        return f"ALLOWED ({path})"
    except OSError:
        return f"refused ({path})"


def symbol_value(base, name):
    """`name` in the dynamic symbol table of the ELF64 object loaded at
    `base`, read from memory, not from the file (the guest may not read
    libc's file): program headers to PT_DYNAMIC, to DT_SYMTAB and
    DT_STRTAB, which glibc lays out with the strings after the symbols."""
    def u16(a): return ctypes.c_uint16.from_address(a).value
    def u32(a): return ctypes.c_uint32.from_address(a).value
    def u64(a): return ctypes.c_uint64.from_address(a).value

    if ctypes.string_at(base, 4) != b"\x7fELF":
        return None
    phoff, phentsize, phnum = u64(base + 0x20), u16(base + 0x36), u16(base + 0x38)
    dyn = None
    for i in range(phnum):
        ph = base + phoff + i * phentsize
        if u32(ph) == 2:   # PT_DYNAMIC
            dyn = base + u64(ph + 0x10)
    if dyn is None:
        return None
    tags = {}
    while u64(dyn) != 0:
        tags[u64(dyn)] = u64(dyn + 8)
        dyn += 16
    if 5 not in tags or 6 not in tags:
        return None
    # The loader relocates these in place on most CPUs, not all.
    strtab, symtab = [v if v >= base else base + v for v in (tags[5], tags[6])]
    want = name.encode() + b"\0"
    for at in range(symtab, strtab, 24):
        if u64(at + 8) and u16(at + 6) and ctypes.string_at(strtab + u32(at), len(want)) == want:
            return u64(at + 8)
    return None


def probe_raw_syscall(path):
    """openat(2) through libc's own syscall(), which no LD_PRELOAD sees: only
    a kernel layer (Landlock, seccomp) can refuse this one. With dlopen of
    libc refused, the guest finds syscall() by reading libc's symbol table
    in memory and calls it at that address."""
    if not sys.platform.startswith("linux"):
        return "skipped (Linux only)"
    # openat's number differs by CPU. Only these two are known here.
    SYS_openat = {"x86_64": 257, "aarch64": 56}.get(os.uname().machine)
    if SYS_openat is None:
        return f"skipped (no openat number for {os.uname().machine})"
    AT_FDCWD = -100
    _, base = libc_file()
    off = symbol_value(base, "syscall") if base else None
    if off is None:
        return "skipped (libc's syscall not found)"
    raw = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_long, ctypes.c_long,
                           ctypes.c_char_p, ctypes.c_long, use_errno=True)(base + off)
    fd = raw(SYS_openat, AT_FDCWD, path.encode(), os.O_RDONLY)
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


stall = "stall" in sys.argv[1:]

while True:
    msg = recv()
    verb, _, rest = msg.partition(" ")
    if verb == "HELLO":
        send(f"HELLO from Python {sys.version.split()[0]}")
        if stall:
            # A guest that stops answering: the host's deadline ends it.
            import time
            time.sleep(3600)
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
        send(f"PROBE dlopen libc by path: {probe_dlopen_libc()}")
        send(f"PROBE raw syscall openat('/etc/passwd'): {probe_raw_syscall('/etc/passwd')}")
        send(f"PROBE connect 1.1.1.1:443: {probe_connect()}")
        send("DONE")
    elif verb == "BYE":
        send("BYE")
        break
