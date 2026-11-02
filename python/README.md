# Python guest

An Aether program (`host.ae`) runs a stock `python3` script (`guest.py`) in a
sandboxed child process and talks to it over a socket. The guest asks the host
for files by name; the host hands one over as an open file descriptor or says
no. Then the guest tries to get round the host, and reports what happened.

No Aether bridge or `libpython` is involved: the child is plain `python3 -I
guest.py`, standard library only.

| File | What it is |
|---|---|
| `host.ae` | The host: the guest's grants, its request policy, the spawn and the conversation |
| `guest.py` | The guest: message framing, the conversation, and the escape probes |
| `../lib/childbox/` | Shared host-side helpers: framing with deadlines, GRANT/DENY, the audit log |

## Run it

You need Aether 0.873.0 or later and `python3` 3.9 or newer. From the repo
root:

```sh
make python
```

That builds `build/python-host`, runs the conversation, then runs a guest
that goes quiet after its first message, which the host kills at the
5-second deadline. Every GRANT, DENY, timeout and kill also goes to
`build/audit.log`.

Contained runs need **Linux with Landlock** (kernel 5.13 or later; 6.7 or
later for the network rules). Elsewhere the spawn refuses and starts nothing.
To see the conversation on macOS anyway, with no sandbox, ask for that by
name:

```sh
make python UNCONTAINED=1
```

On Linux, contained, the guest's escape attempts are all refused:

```
python -> host  PROBE open('/etc/passwd'): refused (Permission denied)
python -> host  PROBE dlopen libc by path: refused (/usr/lib/libc.so.6)
python -> host  PROBE raw syscall openat('/etc/passwd'): refused (Permission denied)
python -> host  PROBE connect 1.1.1.1:443: refused (Operation not permitted)
```

## Use it in your own program

On the **host** side (`host.ae`) you write three things:

1. **What the guest may do on its own.** Here that's Python's own library and
   the script, nothing else:

   ```aether,fragment
   guest = sandbox.new("python-guest") {
       grant_runtime("python3")
       grant_fs_read("${os.getcwd()}/${GUEST}")
   }
   ```

   `grant_runtime("python3")` asks the interpreter where its standard library
   lives, so no platform paths appear in your code.

2. **What the guest may ask for.** A closure from a name to a path on the
   host, or `""` for no. The guest never sees the path, only the open file:

   ```aether,fragment
   policy = |name: string| {
       if name == "config" { return "data/config.txt" }
       return ""
   }
   ```

3. **The spawn and the conversation.** `childbox.start_guest` calls
   `sandbox.spawn_socket` (or `spawn_socket_uncontained`, only when asked),
   and `childbox.converse` runs the exchange with a deadline on every wait.
   Replace `childbox.respond` with your own protocol.

On the **guest** side (`guest.py`) the socket is the descriptor in
`AETHER_IPC_FD` (3). Copy `send`, `recv` and `request` from `guest.py`:

- every message is a 4-byte big-endian length, then the bytes, at most
  65536 bytes;
- to get a file, send `REQUEST <name>`; the reply is `DENY <why>`, or
  `GRANT <name>` followed by the descriptor, taken with `socket.recv_fds`.

The probe functions are only there to show the walls; your guest doesn't need
them.
