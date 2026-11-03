"""The guest's side of Aether's std.guest protocol, for a Python program.

Standard library only. A host starts the program with std.guest (through
sandbox.spawn_socket) and talks to it over the socket named in
AETHER_IPC_FD, in std.ipc's frames: a 4-byte big-endian length, then the
bytes, at most MAX_FRAME. Every message is a verb and its text; Aether's
std/guest/README.md has them all. This is the Python twin of
std.guest.child, so the host drives a Python guest exactly as it drives an
Aether worker:

    import aether_guest

    def main(g):
        greet = g.function("greet")          # mounted by the host
        g.say("hello")
        g.say(greet("python"))               # a CALL; waits for the REPLY
        return 0                             # DONE 0

    aether_guest.open("my-guest").run(main)

CANCEL. A reader thread owns the socket's reads. When the host cancels (a
deadline passed, or guest.cancel), it signals the main thread, so a
`Cancelled` is raised wherever the program is: in a loop, in time.sleep, or
waiting on a call. run() turns it into ERROR "cancelled by host: <reason>".
A program that catches it is told again at its next call, and a host that
gets no answer kills it after its grace.

Python has no lockdown of its own to enter: what holds it is the process
sandbox the host spawned it in (grant_runtime("python3") and its script).
"""

import json
import os
import queue
import signal
import socket
import sys
import threading

MAX_FRAME = 65536
MAX_FILE = 65536


class Cancelled(Exception):
    """The host cancelled the script; str() is the reason."""


class HostFailed(Exception):
    """The host failed a call (FAIL); str() is its message."""


class Guest:
    def __init__(self, sock, name):
        self.sock = sock
        self.name = name
        self.script = ""
        self.probe = False
        self.log_level = -1
        self.cancel = ""
        self.mounts = {}
        self._replies = queue.Queue()
        self._waiting = False
        self._raised = False
        self._main = threading.main_thread().ident

    # ---- frames ------------------------------------------------------

    def _send(self, msg):
        data = msg.encode()
        # A cancel's signal held off until the frame is out whole: a frame
        # cut short would leave the host's stream out of step.
        held = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
        try:
            self.sock.sendall(len(data).to_bytes(4, "big") + data)
        except OSError:
            os._exit(1)        # the host has gone: nobody is left to answer
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, held)

    def _read_exact(self, n):
        got = b""
        while len(got) < n:
            try:
                part = self.sock.recv(n - len(got))
            except OSError:
                part = b""
            if not part:
                os._exit(1)    # the host has gone
            got += part
        return got

    def _hear(self):
        size = int.from_bytes(self._read_exact(4), "big")
        if size > MAX_FRAME:
            os._exit(1)        # out of step: nothing can be read after it
        verb, _, rest = self._read_exact(size).decode("utf-8", "replace").partition(" ")
        return verb, rest

    # ---- before the script -------------------------------------------

    def read_script(self):
        """MOUNT, PROBE and LOGLEVEL, up to SCRIPT. On a protocol error it
        tells the host (ERROR) and exits."""
        while True:
            verb, rest = self._hear()
            if verb == "SCRIPT":
                self.script = rest
                return
            err = ""
            if verb == "MOUNT":
                err = self._mount(rest)
            elif verb == "PROBE":
                self.probe = True
            elif verb == "LOGLEVEL":
                err = self._set_level(rest)
            else:
                err = f"protocol: expected MOUNT, PROBE, LOGLEVEL or SCRIPT, got '{verb}'"
            if err:
                self._send(f"ERROR {err}")
                sys.exit(1)

    def _mount(self, spec):
        parts = spec.split(" ")
        if len(parts) != 3 or not parts[0]:
            return "protocol: MOUNT wants NAME min max"
        name = parts[0]
        try:
            lo, hi = int(parts[1]), int(parts[2])
        except ValueError:
            return f"protocol: MOUNT {name}: arity is not a number"
        if lo < 0 or hi < lo:
            return f"protocol: MOUNT {name}: arity {lo}..{hi} is not 0 <= min <= max"
        self.mounts[name] = (lo, hi)
        return ""

    def _set_level(self, rest):
        try:
            level = int(rest)
        except ValueError:
            level = None
        if level is None or level < -1 or level > 5:
            return f"protocol: LOGLEVEL wants -1 (off) or 0..5, got '{rest}'"
        self.log_level = level
        return ""

    # ---- while the script runs ---------------------------------------

    def _reader(self):
        while True:
            verb, rest = self._hear()
            if verb == "FILE":
                # The descriptor rides on the next byte: one recvmsg takes both.
                try:
                    _payload, fds, _flags, _addr = socket.recv_fds(self.sock, 1, 1)
                except OSError as e:
                    self._replies.put(("VALUE", json.dumps({"granted": False, "why": str(e)})))
                    continue
                self._replies.put(("FILE", fds[0] if fds else -1))
            elif verb in ("REPLY", "FAIL") and self._waiting:
                self._replies.put((verb, rest))
            elif verb == "LOGLEVEL":
                err = self._set_level(rest)
                if err:
                    self._cancelled(err)
            elif verb == "CANCEL":
                self._cancelled(rest or "no reason given")
            else:
                self._cancelled(f"protocol: unexpected '{verb}' while the script runs")

    def _cancelled(self, why):
        if not self.cancel:
            self.cancel = why
        self._replies.put(("CANCEL", self.cancel))
        try:
            signal.pthread_kill(self._main, signal.SIGUSR1)
        except (OSError, ValueError):
            pass

    def _on_signal(self, _signum, _frame):
        if self.cancel and not self._raised:
            self._raised = True
            raise Cancelled(self.cancel)

    def poll(self):
        """"" to go on, or the host's reason for cancelling."""
        return self.cancel

    def function(self, name):
        """A callable for the mounted function `name`, held to its arity."""
        if name not in self.mounts:
            raise NameError(f"{name} is not a function the host mounted")
        lo, hi = self.mounts[name]

        def mounted(*args):
            if len(args) < lo or len(args) > hi:
                raise TypeError(f"{name} takes {lo} to {hi} arguments")
            return self.call(name, list(args))
        mounted.__name__ = name
        return mounted

    def call(self, name, args):
        """Calls the mounted function `name` with `args` and waits for the
        host's answer: the value, or HostFailed, or Cancelled."""
        if self.cancel:
            raise Cancelled(self.cancel)
        self._waiting = True
        try:
            self._send(f"CALL {name} {json.dumps(args)}")
            kind, value = self._replies.get()
        finally:
            self._waiting = False
        if kind == "CANCEL" or self.cancel:
            raise Cancelled(self.cancel)
        if kind == "FAIL":
            raise HostFailed(value or "host function failed")
        if kind == "FILE":
            return self._take_file(value)
        return json.loads(value)

    def _take_file(self, fd):
        if fd < 0:
            return {"granted": False, "why": "no descriptor came with FILE"}
        with os.fdopen(fd, "rb") as f:
            data = f.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            return {"granted": False, "why": f"file larger than {MAX_FILE} bytes"}
        return {"granted": True, "text": data.decode("utf-8", "replace")}

    def say(self, text):
        self._send(f"SAY {text}")

    def log(self, level, text, **tags):
        """A log message at `level` (0 trace .. 5 fatal), sent only at or
        above the host's threshold (off by default)."""
        if self.log_level < 0 or level < self.log_level or level > 5:
            return
        t = json.dumps(tags, separators=(",", ":"))
        self._send(f"LOG {level} {len(t.encode())} {t} {text}")

    def audit(self, line):
        self._send(f"AUDIT {line}")

    def run(self, main):
        """Reads up to the script, then runs main(guest): DONE with its
        return value (0 for None), or ERROR with its exception, or "cancelled
        by host: <reason>". Exits after."""
        self.read_script()
        signal.signal(signal.SIGUSR1, self._on_signal)
        threading.Thread(target=self._reader, daemon=True).start()
        try:
            code = main(self)
            self._send(f"DONE {int(code or 0)}")
        except Cancelled:
            self._send(f"ERROR cancelled by host: {self.cancel}")
        except Exception as e:     # the script's own failure, reported
            if self.cancel:
                self._send(f"ERROR cancelled by host: {self.cancel}")
            else:
                self._send(f"ERROR {type(e).__name__}: {e}")
        sys.exit(0)


def open(name):
    """The channel this process was spawned with (std.guest's launch)."""
    fd = os.environ.get("AETHER_IPC_FD")
    if fd is None or os.environ.get("AETHER_IPC_KIND") != "socket":
        sys.exit(f"{name}: no socket channel to a host; a host starts this with guest.launch")
    return Guest(socket.socket(fileno=int(fd)), name)
