# Python guest

An Aether program (`host.ae`) runs a stock `python3` script (`guest.py`) in a
sandboxed child process and talks to it over a socket, with Aether's
`std.guest`: the same host module, and the same protocol, as the Rexx and
JavaScript workers. The guest asks the host
for files by name; the host hands one over as an open file descriptor or says
no. Then the guest tries to get round the host, and reports what happened.

No Aether bridge or `libpython` is involved: the child is plain `python3 -I
guest.py`, standard library only.

| File | What it is |
|---|---|
| `host.ae` | The host: the guest's grants, its request policy, the spawn and the mounted functions |
| `guest.py` | The guest: the conversation, and the escape probes |
| `aether_guest.py` | The guest's side of `std.guest`'s protocol in Python, standard library only: frames, mounted functions, a granted file, LOG, and CANCEL |
| `../lib/childbox/` | The showcase's conversation, shared by the three hosts, and the audit log |

## Run it

You need an Aether with `std.guest` (see the top-level README) and `python3`
3.9 or newer. From the repo
root:

```sh
make python
```

That builds `build/python-host`, runs the conversation, then runs a guest
that goes quiet after its first message (a `time.sleep(3600)`). At the
5-second deadline the host sends `CANCEL`, and the guest stops politely:

```
  worker: ERROR cancelled by host: deadline of 5000 ms passed
```

Every GRANT, DENY, timeout, cancel and kill also goes to `build/audit.log`.

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

3. **The spawn and the conversation.** `guest.launch` calls
   `sandbox.spawn_socket` (or `guest.launch_uncontained`, only when asked)
   with `-I guest.py` as python3's arguments; `guest.mount` names the
   functions the guest may call, `guest.start` sends its `SCRIPT` (here,
   which run it is), and `guest.next` returns each `CALL`, with a deadline
   on every wait.

On the **guest** side, copy `aether_guest.py` beside your script:

```python
import aether_guest

def main(g):
    request = g.function("host_request")     # mounted by the host
    cfg = request("config")                  # a CALL; waits for the answer
    if cfg["granted"]:
        g.say(cfg["text"])
    return 0                                 # DONE 0

aether_guest.open("my-guest").run(main)
```

A mounted function is held to the arity the host gave it; its arguments
and result cross as JSON, and a granted file arrives as an open
descriptor, read for you into `{"granted": True, "text": ...}`. A host
`FAIL` raises `HostFailed`. A host `CANCEL` raises `Cancelled` wherever
the program is (a reader thread owns the socket and signals the main
thread), and `run` reports it as `ERROR cancelled by host: <reason>`; a
guest that does not answer is killed after the host's grace.

The probe functions are only there to show the walls; your guest doesn't need
them.
