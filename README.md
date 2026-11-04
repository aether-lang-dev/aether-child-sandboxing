# aether-child-sandboxing

> **Status: needs Aether 0.878.0 or later, the first release with
> `std.guest`.** All three guests run on the real calls,
> `sandbox.spawn_socket` and `std.ipc`'s frames (Aether 0.873.0, through
> [aether#3510](https://github.com/aether-lang-dev/aether/pull/3510)), and
> all three hosts drive their guest with Aether's `std.guest`; the Aether
> workers (RexxAE's and `js-worker`) are built on `std.guest.child`, and the
> Python guest speaks the same protocol through `python/aether_guest.py`.
> The Rexx worker is [RexxAE](https://github.com/aether-lang-dev/RexxAE)'s
> (on `std.guest.child` since its 0.878.0 pin);
> the JavaScript worker uses [mquickjs-ae](https://github.com/aether-lang-dev/mquickjs-ae)'s `main`. Discussion and
> decisions: [aether#3503](https://github.com/aether-lang-dev/aether/issues/3503).

An Aether host runs a guest language **in a separate, sandboxed child
process** and talks to it both ways over `std.ipc`. The guest gets one
socket and nothing else, and asks the host for what it needs. A file comes
back as an **open descriptor**, not a path, so the guest never needs the
right to open anything.

There are three showcases with the same conversation, line for line:

| Showcase | The guest | What crosses into the child |
|---|---|---|
| [`python/`](python/README.md) | stock `python3`, standard library only | nothing: no bridge, no `libpython` ABI to match |
| [`rexx/`](rexx/README.md) | [RexxAE](https://github.com/aether-lang-dev/RexxAE), run by RexxAE's own worker process | nothing: the worker is one binary with the engine compiled in |
| [`js/`](js/README.md) | JavaScript on [mquickjs-ae](https://github.com/aether-lang-dev/mquickjs-ae) (the Aether port of Bellard's MicroQuickJS), run by `js-worker` | nothing: the worker is one binary with the engine compiled in |

The JavaScript one is sae's case teased out. sae, the family's page
browser, runs pages' JavaScript, which nobody vetted, and today does it in
its own process on `contrib.quickjs` (quickjs-ng) with a time limit on each
entry into the page (sae began on mquickjs-ae and moved for modern
JavaScript). `js/` is the out-of-process shape for it: untrusted JavaScript
in a child that locks itself down before the first line runs, reaches the
host only through functions the host mounted, and is cancelled from inside
the engine when a deadline passes.

Both hosts start their guest with `sandbox.spawn_socket`, which fails
closed: on macOS and Windows, or on Linux without Landlock, it starts
nothing. Running a guest uncontained is asked for by name
(`make ... UNCONTAINED=1`, which calls `sandbox.spawn_socket_uncontained`).

## What's coded, what's left

**Coded, and observed working on CachyOS (Linux 7.3, Landlock in `lsm=`,
Python 3.14) with an Aether tree that had `std.guest` (the 0.878.0 release has it):**

- `sandbox.spawn_socket` for both guests, failing closed. The Python guest
  gets `grant_runtime("python3")` (its library, asked of python3 itself, no
  platform paths) and read access to its script; the Rexx worker gets no
  grants at all.
- The kernel layer under it: Landlock and the wider seccomp filter. The
  Python raw-syscall probe is refused.
- `std.ipc`'s length-prefixed frames (`ipc.write_frame`,
  `ipc.read_frame_timeout`), a 4-byte big-endian length with a 64 KiB cap;
  the Python guest reads and writes the same header.
- A granted file handed over as an open descriptor on the byte after a
  `GRANT` frame, taken with one receive (`ipc.recv`, or Python's
  `socket.recv_fds`): no `TAKE` step. The host end refuses descriptors
  sent up by a guest (std.ipc's rule).
- A deadline on every exchange, from `std.guest`: when it passes the host
  sends `CANCEL`, and a guest that does not answer within the grace is
  killed. RexxAE's worker stops the script with `rexxae_cancel`, so a
  script waiting in `HOST_HEAR` for a message that never comes ends with
  `cancelled by host` (`make rexx` runs one). The Python guest's reader
  thread signals the main thread, which raises `Cancelled` wherever the
  program is, a `time.sleep(3600)` included (`make python` runs one).
- RexxAE's generic worker (`../RexxAE/worker`): any script, any mounted
  functions, on `std.guest.child`, locking itself down with
  `sandbox.enforce` and no grants before its first step.
- **The protocol in Aether's standard library.** `std.guest` (the host's
  side) and `std.guest.child` (an Aether worker's) were RexxAE's
  `rexxworker` and the shared half of the two workers; the protocol is
  documented once, in Aether's `std/guest/README.md`. The Python guest
  speaks it with `python/aether_guest.py`, standard library only, so all
  three hosts are the same program with other names.
- `js-worker` (`js/worker.ae`): mquickjs-ae's engine compiled in, on
  `std.guest.child`, so `js/host.ae` drives it with `std.guest` as
  `rexx/host.ae` drives RexxAE's. It locks itself down the same way
  (`child.run`), before the first JavaScript runs, and is spawned with no
  grants. What is left in `worker.ae` is engine glue: the ROM's host
  functions, the mounts as JavaScript, and the interrupt hook.
  - **Its standard library is baked at build time**, as MicroQuickJS does
    it: `js/rom` runs mquickjs-ae's ROM generator (its `gen/genengine`,
    used as a library) on the core-only spec, the language plus
    `console.log`, `print` and `performance.now`, with one global added,
    `__host_call`. A host's mounts are JavaScript wrappers round it that
    check their arity and pass their arguments as JSON; it refuses a name
    the host did not mount.
  - **Cancel is in the engine.** MicroQuickJS runs a script to the end in
    one `JS_Eval`, so there are no slices to step; instead it calls an
    interrupt handler every 10000 calls and backward jumps
    (`JS_SetInterruptHandler`), and the worker's looks for a `CANCEL` on
    the socket without waiting. On one it returns 1 and the engine throws
    an error no `catch` can take. A script waiting on the host gets the
    `CANCEL` as its reply. `make js` runs a `while (true)` that catches
    everything, cancelled at the deadline.
  - `make js-test` (`js/test_worker.ae`) holds all of this, contained on
    Linux. With the interrupt handler not installed, its busy-loop case
    fails as it should: `KILLED no answer to CANCEL` instead of the cancel.
- The host's policy (grant or refuse each request by name), and **one
  audit log** (`build/audit.log`) for spawns, GRANTs, DENYs, timeouts,
  cancels, kills and what the Rexx worker's own sandbox refused.
- Everything a guest sends is escaped before it is printed or logged.

**Left to do:**

1. **macOS and Windows** containment. Until then `spawn_socket` refuses
   there, and only the uncontained run, by name, works.
2. ~~**Python has no cancel.**~~ Done: `python/aether_guest.py` takes the
   host's `CANCEL` on a reader thread and interrupts the program, so a
   Python guest stops politely, and is killed only if it does not answer.
3. ~~**The host module's home.**~~ Done: `std.guest` and `std.guest.child`
   in Aether's standard library; nothing here reaches into RexxAE for it.
4. **sae itself** runs pages in its own process. Moving them into a
   worker like `js-worker` would be on quickjs-ng (`contrib.quickjs`,
   whose time limit is the same hook), not on mquickjs-ae.

## Run it

You need Aether 0.878.0 or later (the first release with `std.guest`), a [RexxAE](https://github.com/aether-lang-dev/RexxAE) checkout next to
this one (`../RexxAE`) and `python3` (3.9 or newer). For `make js`, also an
[mquickjs-ae](https://github.com/aether-lang-dev/mquickjs-ae) checkout next to this one (`../mquickjs-ae`, which `js/mqjs`
points at) and `aeb` (v0.333 or later), which builds `js-worker` as it
builds mquickjs-ae, with the engine compiled by the same Aether tree.

```sh
make python
make rexx
make js
make js-test
```

To use an Aether tree other than the one on `PATH`, pass both
`AE=/path/to/aether/build/ae` and `AETHER_HOME=/path/to/aether`.

`make python` runs the conversation, then a guest that goes quiet
(cancelled at the deadline, 5 s, in the middle of a `time.sleep`). `make rexx` builds RexxAE's worker, runs the
conversation, then a script stuck in `HOST_HEAR` (cancelled at the
deadline). `make js` builds `js-worker`, runs the conversation, then a
script in `while (true)` (cancelled at the deadline), a worker that never
answers (killed after the `CANCEL`'s grace) and a frame over 64 KiB
(refused). On macOS all of them refuse to start a guest:

```
spawn refused: sandbox.spawn_socket: cannot contain a child on macOS, so nothing was started (sandbox.spawn_socket_uncontained runs one with no sandbox)
```

and `make python UNCONTAINED=1` (or `rexx`) runs them with no process
sandbox, saying so on every run.

Tested contained on CachyOS (x86_64, Linux 7.3, Python 3.14) and, on
`std.guest`, on Ubuntu 22.04 (x86_64, Linux 5.15, Python 3.10), and
uncontained on macOS (arm64, Python 3.9).

`js/mqjs` is a relative symlink to `../../mquickjs-ae`, with `js/ae` and
`js/gen` beside it (the engine's own imports and its source list resolve
from `js/`, where aeb runs); re-point `js/mqjs` to build against another
checkout. Nothing in mquickjs-ae changes.

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
  python -> host  PROBE dlopen libc by path: refused (/usr/lib/libc.so.6)
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
  worker: ERROR cancelled by host: deadline of 5000 ms passed
  ...
  host -> rexx-stuck  HELLO from the Aether host
  rexx-stuck -> host  SAY waiting for a second message, which never comes
  worker: ERROR cancelled by host: deadline of 5000 ms passed
```

The JavaScript transcript, contained on CachyOS:

```
  host -> js  HELLO from the Aether host
  js -> host  HELLO from MicroQuickJS (mquickjs-ae)
  host -> js  ASK multiply 6 7
  js -> host  ANSWER 42
  js -> host  REQUEST config
  host -> js  GRANT config   (then data/config.txt as an open descriptor)
  js -> host  SAY config says: greeting=hello from a file on the host's disk
  js -> host  REQUEST /etc/passwd
  host -> js  DENY /etc/passwd: not in this guest's policy
  js -> host  SAY the host said no: not in this guest's policy
  js -> host  PROBE load('/etc/passwd'): refused (ReferenceError: variable 'load' is not defined)
  js -> host  PROBE require('fs'): refused (ReferenceError: variable 'require' is not defined)
  js -> host  PROBE std.loadFile / os.open (QuickJS's modules): refused (ReferenceError: variable 'std' is not defined)
  js -> host  PROBE new XMLHttpRequest(): refused (ReferenceError: variable 'XMLHttpRequest' is not defined)
  js -> host  PROBE fetch('https://1.1.1.1/'): refused (ReferenceError: variable 'fetch' is not defined)
  js -> host  PROBE __host_call('readFile', ...) past the mounts: refused (Error: readFile is not a function the host mounted)
  js -> host  DONE
  host -> js  BYE
  js -> host  BYE
  worker: done, exit 0
  ...
  host -> js-stuck  HELLO from the Aether host
  js-stuck -> host  SAY spinning in while (true), catching everything
  worker: ERROR cancelled by host: deadline of 5000 ms passed
  ...
  host -> js-deaf  HELLO from the Aether host
  worker: KILLED no answer to CANCEL within 1000 ms
  ...
  host -> js-big  HELLO from the Aether host
  worker: KILLED frame refused: ipc.read_frame: frame longer than max
```

And `build/audit.log` after the Python and Rexx runs, the host's decisions
and the Rexx worker's reports together:

```
--- session: python
SPAWN python3 (sandbox.spawn_socket)
python GRANT config -> data/config.txt
python DENY /etc/passwd: not in policy
--- session: python-stall
SPAWN python3 (sandbox.spawn_socket)
python-stall TIMEOUT no frame within 5000 ms; CANCEL sent
python-stall ERROR cancelled by host: deadline of 5000 ms passed
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

and after the JavaScript runs:

```
--- session: js
js SPAWN js/target/build/bin/js-worker (sandbox.spawn_socket)
js reports: js-worker locked itself down before the first step: no grants
js reports: probe worker fs.read('/etc/passwd'): refused (/etc/passwd: Permission denied)
js reports: probe worker tcp.connect 1.1.1.1:443: refused (connect failed)
js reports: probe worker raw syscall instruction openat('/etc/passwd'), round std: refused (Permission denied)
js reports: js-worker sandbox refused fs_read /etc/passwd
js reports: js-worker sandbox refused tcp 1.1.1.1:443
js GRANT config -> data/config.txt
js DENY /etc/passwd: not in this guest's policy
--- session: js-stuck
js-stuck SPAWN js/target/build/bin/js-worker (sandbox.spawn_socket)
js-stuck reports: js-worker locked itself down before the first step: no grants
js-stuck TIMEOUT no frame within 5000 ms; CANCEL sent
js-stuck ERROR cancelled by host: deadline of 5000 ms passed
--- session: js-deaf
js-deaf SPAWN ../RexxAE/build/worker/silent (sandbox.spawn_socket)
js-deaf TIMEOUT no frame within 5000 ms; CANCEL sent
js-deaf KILLED no answer to CANCEL within 1000 ms
--- session: js-big
js-big SPAWN js/target/build/bin/js-worker (sandbox.spawn_socket)
js-big reports: js-worker locked itself down before the first step: no grants
js-big KILLED frame refused: ipc.read_frame: frame longer than max
```

The worker that never answers is RexxAE's stand-in (`tests/worker_silent`),
which reads every frame and replies to none. `js-worker` itself cannot be
made to ignore a `CANCEL` from JavaScript: the interrupt handler is the
engine's, and its error cannot be caught. What the kill is for is a worker
that has stopped answering for another reason, a bug or a hang in native
code.

## What a developer writes

All three hosts are the same program with other names: they drive their
guest with Aether's `std.guest`.

**The Python host** (`python/host.ae`):

```aether
perms = sandbox.new("python-guest") {
    grant_runtime("python3")                     // asked of python3 itself
    grant_fs_read("${os.getcwd()}/python/guest.py")
    grant_fs_read("${os.getcwd()}/python/aether_guest.py")
}
policy = |name: string| {                        // what the guest may ask for
    if name == "config" { return "data/config.txt" }
    return ""
}
w, err = guest.launch(perms, "python3", argv, "python", childbox.AUDIT_LOG)   // -I python/guest.py
guest.mount(&w, "host_hear", 0, 0)               // and host_tell, host_request
guest.start(&w, "chat")
e = guest.next(&w)                               // SAY, CALL, DONE, ERROR, KILLED
```

There are two kinds of permission, and they are kept apart on purpose:

- **Grants** say what the guest may do *on its own*: the least it needs to
  start. The process sandbox enforces them, down to the kernel.
- **The policy** says what the guest may *ask for*. The host decides each
  request, and hands over a descriptor rather than a path.

Both kinds end up in the one audit log.

**The Rexx host** (`rexx/host.ae`) drives RexxAE's worker:

```aether
perms = sandbox.new("rexx-guest") {}             // the worker needs nothing
w, err = guest.launch(perms, prog, null, "rexx", childbox.AUDIT_LOG)
guest.mount(&w, "HOST_HEAR", 0, 0)               // and HOST_TELL, HOST_REQUEST
guest.start(&w, source)
e = guest.next(&w)                               // SAY, CALL, DONE, ERROR, KILLED
```

**The JavaScript host** (`js/host.ae`) is the Rexx host with other names:

```aether
perms = sandbox.new("js-guest") {}               // the worker needs nothing
w, err = guest.launch(perms, "js/target/build/bin/js-worker", null, "js", childbox.AUDIT_LOG)
guest.mount(&w, "hostHear", 0, 0)                // and hostTell, hostRequest
guest.start(&w, source)
e = guest.next(&w)                               // SAY, CALL, DONE, ERROR, KILLED
```

**The guests**, in their own languages:

- **Python** (`python/guest.py`): `aether_guest.open("python-guest").run(main)`,
  and the three mounted functions as Python callables:
  `g.function("host_request")("config")` returns `{"granted": True, "text":
  ...}` or `{"granted": False, "why": ...}`. `python/aether_guest.py` is the
  Python twin of `std.guest.child`, standard library only: frames on the
  descriptor named in `AETHER_IPC_FD`, `socket.recv_fds` for a granted file,
  and a reader thread for `CANCEL`.
- **Rexx** (`rexx/chat.rexx`): RexxAE has no system API of its own. The
  host mounts three functions in the worker, `HOST_HEAR()`,
  `HOST_TELL(line)` and `HOST_REQUEST(name)`, and each is a frame on the
  socket. No `ADDRESS` environment is mounted. The worker runs the script
  inside its own `sandbox.enforce` with no grants (RexxAE README, "Worker
  process").
- **JavaScript** (`js/chat.js`): the same, with `hostHear()`,
  `hostTell(line)` and `hostRequest(name)`. `hostRequest` returns an
  object, `{granted: true, text: ...}` or `{granted: false, why: ...}`.
  `print` and `console.log` are `SAY` lines.

**The shared part** is Aether's `std.guest` (the spawn, the frames with
their deadline, cancel and kill, the descriptor hand-over, escaping, the
audit log's lines) and, for the showcase, `lib/childbox`: what the host
says in the conversation, which is the same for every guest.

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
| Python `ctypes.CDLL` of libc by its absolute path | skipped (Linux only) | refused | refused (the preload) |
| Python raw `syscall(openat)` via ctypes, libc's `syscall` found in its symbol table in memory | skipped (Linux only) | **ALLOWED** | refused (Permission denied: Landlock) |
| Python `connect` 1.1.1.1:443 | ALLOWED | refused | refused (Operation not permitted: seccomp) |
| Python guest silent after HELLO (in `time.sleep`) | `cancelled by host` at the deadline | `cancelled by host` | `cancelled by host` at the deadline (5 s) |
| Rexx `ADDRESS SYSTEM` | RC 1 | RC 1 | RC 1 (the engine's own wall) |
| Rexx worker `fs.read` / `tcp.connect` | refused | refused | refused (the worker's own enforce) |
| Rexx script waiting in `HOST_HEAR` | `cancelled by host` at the deadline | `cancelled by host` | `cancelled by host` at the deadline (5 s) |
| JS `load`, `require`, `std`/`os`, `XMLHttpRequest`, `fetch` | ReferenceError | ReferenceError | ReferenceError (the engine's own wall: not in its ROM) |
| JS FFI, native modules, bytecode loading | none in MicroQuickJS | none | none: there is nothing to probe |
| JS `__host_call` with a name the host did not mount | refused | refused | refused (the worker) |
| JS worker `fs.read` / `tcp.connect` | refused | refused | refused (the worker's own enforce) |
| JS worker open round std: on Linux the raw `openat` instruction, round libc and the preload; on macOS libc's `open` | **ALLOWED** (`enforce` alone is std's) | **ALLOWED** | refused (Permission denied: Landlock) |
| JS `while (true)` catching every exception | `cancelled by host` at the deadline | `cancelled by host` | `cancelled by host` at the deadline (5 s) |
| JS worker that never answers (the stand-in) | killed after the `CANCEL`'s grace | killed | killed (1 s after the `CANCEL`) |
| JS `print` of 70000 bytes | frame refused, worker killed | refused | frame refused, worker killed |

The raw-syscall row is the reason for a kernel layer. LD_PRELOAD only sees
calls that go through libc, and any interpreter with an FFI (Python's
`ctypes`, LuaJIT's FFI, Ruby's `fiddle`) can go round it. The preload
refuses a handle on libc however it is named (by name, by path, through a
symlink), so the probe reads libc's dynamic symbol table in memory, finds
`syscall` there, and calls it at that address. Landlock and the seccomp filter cannot be gone round this
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
- The preload's `dlopen` check refused only the bare names `libc.so.6` and
  `libc.so`; libc by its path loaded. Fixed in 0.873.0
  (aether#3510): libc and the loader are matched by basename, by
  where a path leads, and by the handle the loader returns. The preload
  alone is still not a wall against an FFI (the raw-syscall row), which
  is what Landlock is for.
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
  everything from a guest now goes through `guest.printable`.
- The preload replaces libc's `syscall()` and refuses it outright when
  sandboxed, so a raw-syscall probe written in C has to use the
  instruction itself (`js/rom.c`), as the Python one finds `syscall` in
  libc's symbol table. Uncontained on Linux that probe opens
  `/etc/passwd`: the worker's own `enforce` is std's checks, not a kernel
  wall.
- MicroQuickJS names its host functions as C symbols in a ROM generated at
  build time, so a worker cannot add a global from C at run time. One
  fixed native function and JavaScript wrappers for each mount do it
  instead, and keep the protocol's `MOUNT` working unchanged.
- MicroQuickJS refuses two `catch (e)` with the same name at the top level
  of one script ("catch variable already exists"). Upstream does the same
  (Bellard's `7ea5399`), so the port is faithful; a test script uses two
  names.
