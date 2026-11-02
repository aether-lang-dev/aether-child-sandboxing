# aether-child-sandboxing

> **Status: a design for discussion, not a working sandbox yet.** The
> process sandbox these guests need does not exist in Aether, so they run
> without one today; the Rexx worker's own in-process sandbox is real.
> Discussion and decisions: [aether#3503](https://github.com/aether-lang-dev/aether/issues/3503).

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

Nothing in Aether was changed for this repo. The one piece Aether lacks, a
spawn that is both sandboxed and holds the socket (`sandbox.spawn_socket`),
is a stand-in that both hosts have to ask for by name:
`childbox.spawn_socket_uncontained`.

## What's coded, what's left

**Coded, and working today:**

- The two-way conversation over `std.ipc`, on the socketpair that
  `os.run_socket` sets up (fd 3 in the child).
- Length-prefixed messages with a maximum frame size (64 KiB), so a
  payload may hold any byte and a guest cannot grow the host's buffer
  without end.
- Handing a granted file over as an open descriptor (`GRANT`, then `TAKE`,
  then `ipc.send_fd`).
- The host's policy: the guest asks for a file by name, and the host grants
  or refuses it.
- **One audit log** (`build/audit.log`) for both kinds of permission: the
  host's GRANTs and DENYs, and what the guest's own sandbox refused.
- Everything a guest sends is escaped before it is printed or logged.
- Both guests: `python/guest.py`, and `rexx/chat.rexx` with its worker
  (`rexx/guest.ae`) and three mounted functions.
- **Two of the Rexx guest's walls are real now:**
  - RexxAE's own: with no `ADDRESS` environment mounted, `ADDRESS SYSTEM`
    gets RC 1.
  - The worker's: it reads its script, then locks itself down with
    `sandbox.enforce` and no grants before the first step, so its own
    file and network probes are refused.
- The escape probes, which report honestly what gets through.
- After a protocol error the host hangs up and stops the guest, rather than
  waiting on a child that may be blocked writing.

**Left to do, all of it in Aether** (each answered on aether#3503, see
"Decisions" below):

1. **`sandbox.spawn_socket`**: the sandboxed spawn that keeps the socket,
   failing closed. Until it exists, the grants in `python/host.ae` and
   `rexx/host.ae` are built, handed to the stand-in, and ignored, and the
   Python guest's probes all get through.
2. **A kernel layer**, Landlock first, then a wider seccomp filter. Without
   it the Python raw-syscall probe gets round even the preload.
3. **`grant_runtime("python3")`**, a preset so a foreign interpreter can
   read its own library without paths written per platform.
4. **`std.ipc` improvements:** one receive that returns data and an
   optional descriptor (no more `TAKE` step), a host-side refusal of
   descriptors sent up by a guest, and the frame reader written once in
   `std.ipc`, with its cap, replacing the one in `lib/childbox`.
5. **Deadlines:** a host-side deadline on each exchange, and a cancel in
   RexxAE so the worker can stop a script stuck waiting. Today a guest
   that stays silent hangs the host, since a `std.ipc` read cannot time
   out.
6. **A generic RexxAE worker** shipped in RexxAE: any script, any mounted
   functions, enforcing on itself.

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
make AE=$HOME/.aether/versions/v0.871.0/bin/ae AETHER_HOME=$HOME/.aether/versions/v0.871.0
```

Tested on macOS (arm64, Python 3.9) with Aether 0.871.0, and earlier
versions on CachyOS Linux (x86_64, Python 3.14).

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

The RexxAE transcript is the same apart from its probes, and its worker's
reports for the audit log:

```
  rexx -> host  AUDIT rexx-worker locked itself down before the first step: no grants
  ...
  rexx -> host  PROBE ADDRESS SYSTEM cat /etc/passwd: RC 1
  rexx -> host  PROBE worker fs.read('/etc/passwd'): refused (/etc/passwd: Permission denied)
  rexx -> host  PROBE worker tcp.connect 1.1.1.1:443: refused (connect failed)
  rexx -> host  AUDIT rexx-worker sandbox refused fs_read /private/etc/passwd
  rexx -> host  AUDIT rexx-worker sandbox refused tcp 1.1.1.1:443
```

And `build/audit.log` after both runs, the host's decisions and the
worker's together:

```
--- session: python
python GRANT config -> data/config.txt
python DENY /etc/passwd: not in policy
--- session: rexx
rexx reports: rexx-worker locked itself down before the first step: no grants
rexx GRANT config -> data/config.txt
rexx DENY /etc/passwd: not in policy
rexx reports: rexx-worker sandbox refused fs_read /private/etc/passwd
rexx reports: rexx-worker sandbox refused tcp 1.1.1.1:443
```

## What a developer writes

**The host**, about 40 lines (`python/host.ae`; `rexx/host.ae` differs only
in the program and its grants):

```aether
guest = sandbox.new("python-guest") {
    grant_fs_read("/usr/lib/python3*")      // the interpreter's own library
}
policy = |name: string| {                   // what the guest may ask for
    if name == "config" { return "data/config.txt" }
    return ""
}
// Proposed: ch, pid, err = sandbox.spawn_socket(guest, "python3", argv, null)
ch, pid, err = childbox.spawn_socket_uncontained(guest, "python3", argv)
cerr = childbox.converse(ch, "python", policy)
os.wait_pid(pid)
```

There are two kinds of permission, and they are kept apart on purpose:

- **Grants** say what the guest may do *on its own*: the least it needs to
  start. The sandbox enforces them.
- **The policy** says what the guest may *ask for*. The host decides each
  request, and hands over a descriptor rather than a path.

Both kinds end up in the one audit log.

**The guest**, in its own language:

- **Python** (`python/guest.py`): plain `socket` on the descriptor named in
  `AETHER_IPC_FD`, and `socket.recv_fds` to take a granted file.
- **Rexx** (`rexx/chat.rexx`): RexxAE has no system API of its own. The
  worker (`rexx/guest.ae`) mounts three functions, `HOST_HEAR()`,
  `HOST_TELL(line)` and `HOST_REQUEST(name)`, and each one is a message on
  the socket. It mounts no `ADDRESS` environment, and it runs the script
  inside its own `sandbox.enforce` with no grants:

  ```aether
  worker = sandbox.new("rexx-worker") {
  }
  sandbox.enforce(worker, audit_log) callback {   // audit_log: a trusted name
      audit_log("rexx-worker locked itself down before the first step: no grants")
      run(ctx, &w)
  }
  ```

**The shared part** (`lib/childbox`): the framing, the descriptor
handshake, the host's half of the conversation, the audit log, escaping,
and the stand-in spawn.

## The proposed API

```aether
sandbox.spawn_socket(perms: sandbox.Grants, prog: string, argv: ptr, env: ptr) -> (int, int, string)
```

`os.run_socket`'s channel (fd 3, `AETHER_IPC_KIND=socket`) with
`spawn_sandboxed`'s containment, held to `perms`. It fails closed: where it
cannot contain the child (macOS and Windows today) it refuses to start it.
A host that wants an uncontained child has to ask for one explicitly, as
both hosts here do with `childbox.spawn_socket_uncontained` until the real
call exists.

What each probe should report as the containment grows:

| Probe | Today | Preload only | Preload + kernel layer |
|---|---|---|---|
| Python `open('/etc/passwd')` | ALLOWED | refused | refused |
| Python raw `syscall(openat)` via ctypes | ALLOWED | **ALLOWED** | refused (Landlock) |
| Python `connect` | ALLOWED | refused | refused (Landlock on Linux 6.7+, seccomp before) |
| Rexx `ADDRESS SYSTEM` | RC 1 (the engine's own wall) | RC 1 | RC 1 |
| Rexx worker `fs.read` / `tcp.connect` | refused (the worker's own enforce) | refused | refused |

The raw-syscall row is the reason for a kernel layer. LD_PRELOAD only sees
calls that go through libc, and any interpreter with an FFI (Python's
`ctypes`, LuaJIT's FFI, Ruby's `fiddle`) can go round it. Landlock and a
wider seccomp filter cannot be bypassed this way, and neither needs root
or user namespaces.

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
   against it. (The showcase uses that framing now, in `lib/childbox`.)
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
   on each exchange and a cancel in the engine.

## Found along the way

- `send` and `reply` are reserved words (actors), and so is `try`, so the
  natural names for protocol code fail to compile. Here they are `tell`
  and `hear`.
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
