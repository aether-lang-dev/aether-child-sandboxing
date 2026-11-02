# aether-child-sandboxing

> **Status: needs an Aether release that does not exist yet.** This
> branch runs on the real calls, `sandbox.spawn_socket` and `std.ipc`'s
> frames, which are on the aether branch `feat/child-sandboxing` (over
> 0.872.0) and in no release. `main` stays on 0.871.0, with the stand-in,
> until a release contains them. RexxAE's worker is on its local branch
> `feat/generic-worker`. Discussion and decisions:
> [aether#3503](https://github.com/aether-lang-dev/aether/issues/3503).

An Aether host runs a guest language **in a separate, sandboxed child
process** and talks to it both ways over `std.ipc`. The guest gets one
socket and nothing else, and asks the host for what it needs. A file comes
back as an **open descriptor**, not a path, so the guest never needs the
right to open anything.

There are two showcases with the same conversation, line for line:

| Showcase | The guest | What crosses into the child |
|---|---|---|
| `python/` | stock `python3`, standard library only | nothing: no bridge, no `libpython` ABI to match |
| `rexx/` | RexxAE, run by RexxAE's own worker process | nothing: the worker is one binary with the engine compiled in |

Both hosts start their guest with `sandbox.spawn_socket`, which fails
closed: on macOS and Windows, or on Linux without Landlock, it starts
nothing. Running a guest uncontained is asked for by name
(`make ... UNCONTAINED=1`, which calls `sandbox.spawn_socket_uncontained`).

## What's coded, what's left

**Coded, and observed working on CachyOS (Linux 7.3, Landlock in `lsm=`,
Python 3.14) with the aether branch above:**

- `sandbox.spawn_socket` for both guests, failing closed. The Python guest
  gets `grant_runtime("python3")` (its library, asked of python3 itself, no
  platform paths) and read access to its script; the Rexx worker gets no
  grants at all.
- The kernel layer under it: Landlock and the wider seccomp filter. The
  Python raw-syscall probe is refused.
- `std.ipc`'s length-prefixed frames (`ipc.write_frame`,
  `ipc.read_frame_timeout`), a 4-byte big-endian length with a 64 KiB cap;
  the Python guest reads and writes the same header. The framing code in
  `lib/childbox` is gone.
- A granted file handed over as an open descriptor on the byte after a
  `GRANT` frame, taken with one receive (`ipc.recv`, or Python's
  `socket.recv_fds`): no `TAKE` step. The host end refuses descriptors
  sent up by a guest (std.ipc's rule).
- A deadline on every exchange. The Python host hangs up and kills a guest
  that stays quiet past it (`make python` runs one). The Rexx host uses
  RexxAE's worker, whose host side sends `CANCEL` when the deadline passes;
  the worker stops the script with `rexxae_cancel`, so a script waiting in
  `HOST_HEAR` for a message that never comes ends with
  `cancelled by host` (`make rexx` runs one), and a worker that does not
  answer is killed.
- RexxAE's generic worker (`../RexxAE/worker`): any script, any mounted
  functions, locking itself down with `sandbox.enforce` and no grants
  before its first step. `rexx/guest.ae` is gone.
- The host's policy (grant or refuse each request by name), and **one
  audit log** (`build/audit.log`) for spawns, GRANTs, DENYs, timeouts,
  cancels, kills and what the Rexx worker's own sandbox refused.
- Everything a guest sends is escaped before it is printed or logged.

**Left to do:**

1. **A release.** An Aether release containing `sandbox.spawn_socket`,
   `grant_runtime`, the kernel layer and `std.ipc`'s frames, `recv` and
   deadlines; then RexxAE's worker on its `main`, and this branch on `main`.
2. **macOS and Windows** containment. Until then `spawn_socket` refuses
   there, and only the uncontained run, by name, works.
3. **Python has no cancel.** Its host can only kill a quiet guest; a
   Python guest would need its own `CANCEL` handling to stop politely.

## Run it

You need an Aether toolchain built from the aether branch
`feat/child-sandboxing`, a RexxAE checkout next to this one (`../RexxAE`, on
`feat/generic-worker`) and `python3` (3.9 or newer).

```sh
make python AE=/path/to/aether/build/ae AETHER_HOME=/path/to/aether
make rexx   AE=/path/to/aether/build/ae AETHER_HOME=/path/to/aether
```

`make python` runs the conversation, then a guest that goes quiet (killed
at the deadline, 5 s). `make rexx` builds RexxAE's worker, runs the
conversation, then a script stuck in `HOST_HEAR` (cancelled at the
deadline). On macOS both refuse to start a guest:

```
spawn refused: sandbox.spawn_socket: cannot contain a child on macOS, so nothing was started (sandbox.spawn_socket_uncontained runs one with no sandbox)
```

and `make python UNCONTAINED=1` (or `rexx`) runs them with no process
sandbox, saying so on every run.

Tested contained on CachyOS (x86_64, Linux 7.3, Python 3.14), and
uncontained on macOS (arm64, Python 3.9).

## The conversation

Either side can start an exchange. The host asks the guest a question; the
guest asks the host for files, and is given one and refused another. Then
the guest tries to help itself. As observed on CachyOS, contained:

```
  host -> python  HELLO from the Aether host
  python -> host  HELLO from Python 3.14.9
  host -> python  ASK multiply 6 7
  python -> host  ANSWER 42
  python -> host  REQUEST config
  host -> python  GRANT config   (then data/config.txt as an open descriptor)
  python -> host  SAY config says: greeting=hello from a file on the host's disk
  python -> host  REQUEST /etc/passwd
  host -> python  DENY /etc/passwd: not in this guest's policy
  python -> host  SAY the host said no: /etc/passwd: not in this guest's policy
  python -> host  PROBE open('/etc/passwd'): refused (Permission denied)
  python -> host  PROBE raw syscall openat('/etc/passwd'): refused (Permission denied)
  python -> host  PROBE connect 1.1.1.1:443: refused (Operation not permitted)
  python -> host  DONE
  host -> python  BYE
  python -> host  BYE
```

The RexxAE transcript is the same apart from its probes, which go to the
audit log, and the guests that stall:

```
  rexx -> host  PROBE ADDRESS SYSTEM cat /etc/passwd: RC 1
  ...
  host -> python-stall  ASK multiply 6 7
conversation ended: guest went quiet or broke the framing: timed out: no message within 5000 ms
  host: guest killed
  ...
  host -> rexx-stuck  HELLO from the Aether host
  rexx-stuck -> host  SAY waiting for a second message, which never comes
  worker: ERROR cancelled by host: deadline of 5000 ms passed
```

And `build/audit.log` after both, the host's decisions and the Rexx
worker's reports together:

```
--- session: python
SPAWN python3 (sandbox.spawn_socket)
python GRANT config -> data/config.txt
python DENY /etc/passwd: not in policy
--- session: python-stall
SPAWN python3 (sandbox.spawn_socket)
python-stall TIMEOUT or bad frame: timed out: no message within 5000 ms
python-stall KILLED after: guest went quiet or broke the framing: timed out: no message within 5000 ms
--- session: rexx
rexx SPAWN ../RexxAE/build/worker/rexxae-worker (sandbox.spawn_socket)
rexx reports: rexxae-worker locked itself down before the first step: no grants
rexx reports: probe worker fs.read('/etc/passwd'): refused (/etc/passwd: Permission denied)
rexx reports: probe worker tcp.connect 1.1.1.1:443: refused (connect failed)
rexx reports: rexxae-worker sandbox refused fs_read /etc/passwd
rexx reports: rexxae-worker sandbox refused tcp 1.1.1.1:443
rexx GRANT config -> data/config.txt
rexx DENY /etc/passwd: not in this guest's policy
rexx reports: rexxae-worker refused ADDRESS system: no environment is mounted
--- session: rexx-stuck
rexx-stuck SPAWN ../RexxAE/build/worker/rexxae-worker (sandbox.spawn_socket)
rexx-stuck reports: rexxae-worker locked itself down before the first step: no grants
rexx-stuck TIMEOUT no frame within 5000 ms; CANCEL sent
rexx-stuck ERROR cancelled by host: deadline of 5000 ms passed
```

## What a developer writes

**The Python host** (`python/host.ae`):

```aether
guest = sandbox.new("python-guest") {
    grant_runtime("python3")                     // asked of python3 itself
    grant_fs_read("${os.getcwd()}/python/guest.py")
}
policy = |name: string| {                        // what the guest may ask for
    if name == "config" { return "data/config.txt" }
    return ""
}
ch, pid, err = childbox.start_guest(guest, "python3", argv, 0)   // spawn_socket
cerr = childbox.converse(ch, "python", policy)   // every wait has a deadline
```

There are two kinds of permission, and they are kept apart on purpose:

- **Grants** say what the guest may do *on its own*: the least it needs to
  start. The process sandbox enforces them, down to the kernel.
- **The policy** says what the guest may *ask for*. The host decides each
  request, and hands over a descriptor rather than a path.

Both kinds end up in the one audit log.

**The Rexx host** (`rexx/host.ae`) drives RexxAE's worker through its host
module, `rexxworker`, built with `--lib ../RexxAE/worker`:

```aether
guest = sandbox.new("rexx-guest") {}             // the worker needs nothing
w, err = rexxworker.launch(guest, prog, "rexx", childbox.AUDIT_LOG)
rexxworker.mount(&w, "HOST_HEAR", 0, 0)          // and HOST_TELL, HOST_REQUEST
rexxworker.start(&w, source)
e = rexxworker.next(&w)                          // SAY, CALL, DONE, ERROR, KILLED
```

**The guests**, in their own languages:

- **Python** (`python/guest.py`): plain `socket` on the descriptor named in
  `AETHER_IPC_FD`, the 4-byte frame header, and `socket.recv_fds` to take
  a granted file.
- **Rexx** (`rexx/chat.rexx`): RexxAE has no system API of its own. The
  host mounts three functions in the worker, `HOST_HEAR()`,
  `HOST_TELL(line)` and `HOST_REQUEST(name)`, and each is a frame on the
  socket. No `ADDRESS` environment is mounted. The worker runs the script
  inside its own `sandbox.enforce` with no grants (RexxAE README, "Worker
  process").

**The shared part** (`lib/childbox`): the spawn, the frames with their
deadline, the descriptor hand-over, the host's half of the conversation, the
audit log and escaping.

## The API

```aether
sandbox.spawn_socket(perms: sandbox.Grants, prog: string, argv: ptr, env: ptr) -> (int, int, string)
```

`os.run_socket`'s channel (fd 3, `AETHER_IPC_KIND=socket`) with
`spawn_sandboxed`'s containment and a kernel layer, held to `perms`. It
fails closed: where it cannot contain the child it refuses to start it.
`sandbox.spawn_socket_uncontained` is the explicit alternative.

What each probe reports. "Contained" is observed on CachyOS (Linux 7.3,
Landlock and seccomp); "Preload only" is what the LD_PRELOAD layer alone
would allow, from its code:

| Probe | Uncontained (macOS) | Preload only | Contained (Linux, observed) |
|---|---|---|---|
| Python `open('/etc/passwd')` | ALLOWED | refused | refused (Permission denied) |
| Python raw `syscall(openat)` via ctypes, libc loaded by path | skipped (Linux only) | **ALLOWED** | refused (Permission denied: Landlock) |
| Python `connect` 1.1.1.1:443 | ALLOWED | refused | refused (Operation not permitted: seccomp) |
| Python guest silent after HELLO | killed at the deadline | killed | killed at the deadline (5 s) |
| Rexx `ADDRESS SYSTEM` | RC 1 | RC 1 | RC 1 (the engine's own wall) |
| Rexx worker `fs.read` / `tcp.connect` | refused | refused | refused (the worker's own enforce) |
| Rexx script waiting in `HOST_HEAR` | `cancelled by host` at the deadline | `cancelled by host` | `cancelled by host` at the deadline (5 s) |

The raw-syscall row is the reason for a kernel layer. LD_PRELOAD only sees
calls that go through libc, and any interpreter with an FFI (Python's
`ctypes`, LuaJIT's FFI, Ruby's `fiddle`) can go round it: the preload
refuses `dlopen("libc.so.6")` by that name, and the probe loads libc by its
path instead. Landlock and the seccomp filter cannot be gone round this
way, and neither needs root or user namespaces.

## Decisions

The questions this README first asked were answered by Adil on
[aether#3503](https://github.com/aether-lang-dev/aether/issues/3503). In
short:

1. **Name and home:** `sandbox.spawn_socket(perms, ...)`. Containment is
   the point, so it lives in `std.sandbox`; an optional `perms` on
   `os.run_socket` would make a forgotten one silent.
2. **One receive** returning `(data, fd or -1, err)`, which removes the
   `TAKE` step. With it, a **direction rule**: the host refuses
   descriptors sent up by a guest.
3. **Framing:** length-prefixed with a maximum frame size, not lines,
   written once in `std.ipc`. Adil will write a chunked fuzz target
   against it. (The showcase uses `std.ipc`'s frames now.)
4. **Start-up grants, two answers for two kinds of guest:**
   - an Aether worker locks itself down after it starts, with
     `sandbox.enforce` before the first step (done here);
   - a foreign interpreter gets a preset such as `grant_runtime("python3")`.
     Not one API for both.
5. **The kernel layer, in order:**
   - Landlock first, which on its own refuses the raw `openat` probe;
   - then a seccomp filter wider than today's process-creation fence (at
     least socket, connect, execve, `io_uring_setup`, ptrace);
   - a seccomp user-notify broker last, if ever, since it mostly repeats
     the policy the protocol already has.

   Landlock's network rules need Linux 6.7, so older kernels still need
   seccomp to refuse connect.
6. **Policy shape:** keep declarative grants and a closure for requests,
   with every GRANT and DENY in the audit log (done here).
7. **macOS and Windows:** fail closed, as `spawn_sandboxed` does on macOS
   today. Uncontained only on an explicit request from the host (done
   here, by name).
8. **A generic RexxAE worker:** yes, enforcing on itself, with a deadline
   on each exchange and a cancel in the engine. Done: RexxAE's
   `worker/`, used by `rexx/host.ae`.

## Found along the way

- `send`, `reply`, `spawn` and `message` are reserved words (actors), and
  so is `try`, so the natural names for protocol code fail to compile.
  Here they are `tell`, `hear`, `answer` and `start_guest`.
- The preload's `dlopen` check refuses only the bare names `libc.so.6` and
  `libc.so`; libc by its path loads. Under the kernel layer that gains a
  guest nothing (the raw `openat` is still refused), but the preload alone
  is not a wall against an FFI.
- `ae build --lib X` replaces the default `lib/` search path, so a program
  that uses a compiled library *and* a local module needs `--lib lib --lib X`
  ([aether#3504](https://github.com/aether-lang-dev/aether/issues/3504)).
- A closure passed as `fn` has no known result type, so the caller writes
  `string path = policy(name)`.
- Passing a struct where another module's function takes a pointer to it
  is not caught by Aether's checker; the C compiler stops it instead.
- RexxAE ends a `WHEN ... THEN DO` block at the next `WHEN` or `OTHERWISE`,
  not at its own `END`, and a classic `THEN DO ... END` followed by
  `OTHERWISE` fails to parse. `rexx/chat.rexx` uses `IF` blocks instead.
  This needs checking against RexxJS.
- A RexxAE library built by a different Aether than the program refuses to
  link, with a clear message naming both versions. Hence the note under
  "Run it".
- The guest-supplied request name was printed unescaped (Adil's catch);
  everything from a guest now goes through `childbox.printable`.
