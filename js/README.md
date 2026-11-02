# JavaScript guest

An Aether program (`host.ae`) runs a JavaScript file (`chat.js`) in
`js-worker`, a sandboxed child process with
[mquickjs-ae](https://github.com/aether-lang-dev/mquickjs-ae) (the Aether
port of Bellard's MicroQuickJS) compiled in, and talks to it over a socket.
The script asks the host for files by name; the host hands one over as an
open file descriptor or says no.

This is the shape of running untrusted page JavaScript outside the program
that shows the page, as sae could. The engine has no file, network, module
or FFI API at all; its only way out is the functions the host mounts. Like
RexxAE's worker, `js-worker` locks itself down with `sandbox.enforce` and no
grants before any JavaScript runs.

| File | What it is |
|---|---|
| `host.ae` | The host: the mounted functions, the request policy, the spawn and the conversation |
| `worker.ae` | `js-worker`: the engine, the worker protocol, the lockdown, cancel |
| `chat.js` | The script: the conversation, file requests, and the escape probes |
| `stuck.js` | `while (true) {}`, cancelled at the deadline |
| `big.js` | A message over the 64 KiB limit, refused |
| `test_worker.ae` | `make js-test`: 11 cases, contained on Linux |
| `rom/`, `rom.c`, `.build.ae`, `mqjs`, `ae`, `gen` | Building the engine with the one global the host needs (`__host_call`) |

`js-worker` speaks the same protocol as RexxAE's worker, so `host.ae` drives
it with RexxAE's `rexxworker` module.

## Run it

You need Aether 0.873.0 or later, `aeb` v0.333 or later, and checkouts of
RexxAE (`../RexxAE`) and mquickjs-ae (`../mquickjs-ae`) next to this repo.
From the repo root:

```sh
make js
make js-test
```

`make js` builds `js-worker` and `build/js-host`, runs the conversation, then
three failure cases:
- a script stuck in `while (true)` (cancelled at the 5-second deadline);
- a worker that never answers (killed);
- a message over the limit (refused).

Contained runs need **Linux with Landlock**. Elsewhere the spawn refuses and
starts nothing; `make js UNCONTAINED=1` runs it with no process sandbox,
asked for by name.

## Use it in your own program

As with the Rexx worker, `js-worker` runs **any** script; you don't rebuild
it.
1. Launch it with `rexxworker.launch`, granting nothing.
2. Mount the functions your scripts may call (`rexxworker.mount(&w,
   "hostRequest", 1, 1)`) and send the script.
3. Answer `CALL` events in a loop on `rexxworker.next`, which applies the
   deadline.

See `host.ae`, and `../rexx/README.md` for the same steps in more detail.

In JavaScript, a mounted function is a plain global function:
`var cfg = hostRequest('config')` returns `{granted, text, why}`. Arguments
and results cross as JSON.

A cancel works even in a script that catches every error: the engine checks
for it every 10,000 calls and loop jumps, and throws an error no `catch` can
take.
