"""The Python showcase's guest: stock python3, standard library only.

It is started by python/host.ae with one end of a socket at the descriptor
named in AETHER_IPC_FD (3), and speaks Aether's std.guest protocol through
aether_guest.py beside it: the host drives it as it drives RexxAE's and the
JavaScript worker. Its only way out is three functions the host mounts:
    host_hear()          the host's next message
    host_tell(line)      a message to the host
    host_request(name)   ask the host for a file: {granted, text} or {granted, why}
It answers the host's questions, asks for the files it wants, then tries to
get round the host, and reports what happened. The host's SCRIPT says which
run this is: "chat", or "stall" (goes quiet after HELLO; the host's deadline
cancels it, and the cancel interrupts the sleep).
"""

import ctypes
import importlib.util
import os
import socket
import sys
import time

# aether_guest.py is beside this script. -I leaves this directory off
# sys.path, and the sandbox grants the two files, not the directory an
# import would list, so it is loaded by its path.
sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "aether_guest", os.path.join(os.path.dirname(os.path.abspath(__file__)), "aether_guest.py"))
aether_guest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aether_guest)


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


def main(g):
    hear = g.function("host_hear")
    tell = g.function("host_tell")
    request = g.function("host_request")
    stall = g.script.strip() == "stall"
    while True:
        verb, _, rest = hear().partition(" ")
        if verb == "HELLO":
            tell(f"HELLO from Python {sys.version.split()[0]}")
            if stall:
                # A guest that stops answering: the host's deadline cancels it.
                time.sleep(3600)
        elif verb == "ASK":
            op, a, b = rest.split()
            tell(f"ANSWER {int(a) * int(b)}" if op == "multiply" else f"ANSWER ? {op}")

            # The guest's own turn: it asks the host for things.
            cfg = request("config")
            tell(f"SAY config says: {cfg['text'].strip()}" if cfg["granted"] else f"SAY no config: {cfg['why']}")
            pw = request("/etc/passwd")
            tell("SAY read /etc/passwd through the host" if pw["granted"]
                 else f"SAY the host said no: {pw['why']}")

            # And it tries to help itself.
            tell(f"PROBE open('/etc/passwd'): {probe_open('/etc/passwd')}")
            tell(f"PROBE dlopen libc by path: {probe_dlopen_libc()}")
            tell(f"PROBE raw syscall openat('/etc/passwd'): {probe_raw_syscall('/etc/passwd')}")
            tell(f"PROBE connect 1.1.1.1:443: {probe_connect()}")
            tell("DONE")
        elif verb == "BYE":
            tell("BYE")
            return 0


aether_guest.open("python-guest").run(main)
