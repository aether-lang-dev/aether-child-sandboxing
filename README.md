# aether-child-sandboxing

> **Status: a design for discussion, not a working sandbox.** The guests
> below run uncontained today; see "The proposed API" and the questions at
> the end.

An Aether host runs a guest language **in a separate, sandboxed child
process** and talks to it both ways over `std.ipc`. The guest gets one
socket and nothing else, and asks the host for what it needs. A file comes
back as an **open descriptor**, not a path, so the guest never needs the
right to open anything.

There are two showcases with the same conversation, line for line:

| Showcase | The guest | What crosses into the child |
|---|---|---|
| `python/` | stock `python3`, standard library only | nothing: no bridge, no `libpython` ABI to match |
| `rexx/` | RexxAE, run by a small Aether worker | the worker links RexxAE's Aether library |

**This is a design for discussion, not a finished feature.** The one piece
Aether lacks, a spawn that is both sandboxed and holds the socket, is a
clearly marked stand-in here (see "The proposed API"). So today the guests
are *not* contained, and their escape probes say `ALLOWED`. Nothing in
Aether was changed for this repo.

## Run it

You need an Aether toolchain, a RexxAE checkout next to this one
(`../RexxAE`) and `python3` (3.9 or newer).

```sh
make python        # the Python showcase
make rexx          # builds RexxAE's library and the Rexx worker, then runs
```

The RexxAE library and the programs here must come from the same Aether
release, since they share one runtime. With several releases installed,
choose one for both:

```sh
make AE=$HOME/.aether/versions/v0.870.0/bin/ae AETHER_HOME=$HOME/.aether/versions/v0.870.0
```

Tested on macOS (arm64, Python 3.9) and CachyOS Linux (x86_64, Python
3.14), with Aether 0.869.0 and 0.870.0.

## The conversation

Either side can start an exchange. The host asks the guest a question; the
guest asks the host for files, and is given one and refused another. Then
the guest tries to help itself.

```
  host -> python  HELLO from the Aether host
  python -> host  HELLO from Python 3.14.8
  host -> python  ASK multiply 6 7
  python -> host  ANSWER 42
  python -> host  REQUEST config
  host -> python  GRANT config   (then data/config.txt as an open descriptor)
  python -> host  SAY config says: greeting=hello from a file on the host's disk
  python -> host  REQUEST /etc/passwd
  host -> python  DENY /etc/passwd: not in this guest's policy
  python -> host  SAY the host said no: /etc/passwd: not in this guest's policy
  python -> host  PROBE open('/etc/passwd'): ALLOWED
  python -> host  PROBE raw syscall openat('/etc/passwd'): ALLOWED
  python -> host  PROBE connect 1.1.1.1:443: ALLOWED
  python -> host  DONE
  host -> python  BYE
  python -> host  BYE
```

The RexxAE transcript is the same, except for its probes:

```
  rexx -> host  PROBE ADDRESS SYSTEM cat /etc/passwd: RC 1
  rexx -> host  PROBE worker fs.read('/etc/passwd'): ALLOWED
  rexx -> host  PROBE worker tcp.connect 1.1.1.1:443: ALLOWED
```

## What a developer writes

**The host**, about 30 lines (`python/host.ae`; `rexx/host.ae` differs only
in the program and its grants):

```aether
guest = sandbox.new("python-guest") {
    grant_fs_read("/usr/lib/python3*")      // the interpreter's own library
}
policy = |name: string| {                   // what the guest may ask for
    if name == "config" { return "data/config.txt" }
    return ""
}
ch, pid, err = childbox.spawn_guest(guest, "python3", argv)   // proposed API
cerr = childbox.converse(ch, "python", policy)
os.wait_pid(pid)
```

There are two kinds of permission, and they are kept apart on purpose:

- **Grants** say what the guest may do *on its own*: the least it needs to
  start. The sandbox enforces them.
- **The policy** says what the guest may *ask for*. The host decides each
  request, and hands over a descriptor rather than a path.

**The guest**, in its own language:

- **Python** (`python/guest.py`): plain `socket` on the descriptor named in
  `AETHER_IPC_FD`, and `socket.recv_fds` to take a granted file.
- **Rexx** (`rexx/chat.rexx`): RexxAE has no system API of its own. The
  worker (`rexx/guest.ae`) mounts three functions, `HOST_HEAR()`,
  `HOST_TELL(line)` and `HOST_REQUEST(name)`, and each one is a message on
  the socket. It mounts no `ADDRESS` environment, which is why
  `ADDRESS SYSTEM` gets RC 1.

**The shared part** (`lib/childbox`): line framing over `std.ipc`, the
descriptor handshake, the host's half of the conversation, and the
stand-in spawn.

## The proposed API

```aether
os.run_socket_sandboxed(perms: sandbox.Grants, prog: string, argv: ptr, env: ptr) -> (int, int, string)
```

It would combine `os.run_socket`'s channel (fd 3, `AETHER_IPC_KIND=socket`)
with `spawn_sandboxed`'s containment (the preload and the kernel filters,
held to `perms`). `childbox.spawn_guest` is where it would be called. Today
`spawn_guest` calls plain `os.run_socket` instead, and prints a warning that
the guest is not contained.

What each probe should report as the containment grows:

| Probe | Stand-in (today) | Preload only | Preload + kernel layer |
|---|---|---|---|
| Python `open('/etc/passwd')` | ALLOWED | refused | refused |
| Python raw `syscall(openat)` via ctypes | ALLOWED | **ALLOWED** | refused |
| Python `connect` | ALLOWED | refused | refused |
| Rexx `ADDRESS SYSTEM` | RC 1 (the engine's own wall) | RC 1 | RC 1 |
| Rexx worker `fs.read` / `tcp.connect` | ALLOWED | refused | refused |

The raw-syscall row is the reason for a kernel layer. LD_PRELOAD only sees
calls that go through libc, and any interpreter with an FFI (Python's
`ctypes`, LuaJIT's FFI, Ruby's `fiddle`) can go round it. Landlock (file
paths, Linux 5.13 and later) and a wider seccomp filter (sockets, exec,
`io_uring_setup`, ptrace and the like) cannot be bypassed this way, and
neither needs root or user namespaces.

## Questions for Nic and Adil

1. **Name and home.** Should it be `os.run_socket_sandboxed(perms, ...)`,
   `sandbox.spawn_socket(perms, ...)`, or an optional `perms` argument on
   `os.run_socket`?
2. **Descriptors on a byte stream.** A granted file takes three steps here:
   `GRANT`, then `TAKE`, then the descriptor. A plain `ipc.read` that
   reaches a descriptor's bytes drops the descriptor, and `ipc.recv_fd`
   refuses a message that carries none. Should there be a single receive
   that returns `(data, fd or -1, err)`, so a guest can just read?
3. **Framing.** Both guests and the host re-implement newline framing.
   Should `std.ipc` offer framed messages, by line or length-prefixed?
4. **Interpreter start-up.** `python3` must read its standard library before
   it can import anything, so its grants carry interpreter paths that differ
   per platform. Should there be a preset, such as
   `grant_runtime("python3")`, or should the host start the interpreter
   first and then lock it down, as Capsicum's `cap_enter` does? (The dynamic
   loader opens libraries without libc, so the preload never sees those.)
5. **The kernel layer.** Landlock plus a wider seccomp filter in
   `spawn_sandboxed`, and later perhaps a seccomp user-notify broker that
   checks every `openat` the guest makes. How far, and in which order?
6. **Policy shape.** Here the policy is a closure from a name to a path.
   Should it be declarative instead, like grants, and recorded by
   `std.sandbox`'s audit?
7. **macOS and Windows.** `spawn_sandboxed` fails on macOS. Should the
   socket variant fail as well, or run uncontained with a warning, as the
   stand-in does?
8. **Two walls for Rexx.** RexxAE refuses `ADDRESS SYSTEM` by itself, and
   the process sandbox would also hold if the engine had a bug. Is a
   generic RexxAE worker (any script, any set of mounted functions) worth
   shipping in RexxAE?

## Found along the way

- `send` and `reply` are reserved words (actors), so the natural names for
  protocol code fail to compile. Here they are `tell` and `hear`.
- `ae build --lib X` replaces the default `lib/` search path, so a program
  that uses a compiled library *and* a local module needs `--lib lib --lib X`.
- A closure passed as `fn` has no known result type, so the caller writes
  `string path = policy(name)`.
- RexxAE ends a `WHEN ... THEN DO` block at the next `WHEN` or `OTHERWISE`,
  not at its own `END`, and a classic `THEN DO ... END` followed by
  `OTHERWISE` fails to parse. `rexx/chat.rexx` uses `IF` blocks instead.
  This needs checking against RexxJS.
- A RexxAE library built by a different Aether than the program refuses to
  link, with a clear message naming both versions. Hence the note under
  "Run it".
