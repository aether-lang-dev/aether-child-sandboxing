# Rexx guest

An Aether program (`host.ae`) runs a Rexx script (`chat.rexx`) in
[RexxAE](https://github.com/aether-lang-dev/RexxAE)'s worker process, in a
sandboxed child, and talks to it over a socket. The script asks the host for
files by name; the host hands one over as an open file descriptor or says no.

The worker is RexxAE's generic one: one binary with the Rexx engine compiled
in. It locks itself down with `sandbox.enforce` and no grants before the
script's first instruction, so it is walled in twice: by the process sandbox
around it and by its own lockdown. A script has no file, network or process
access of its own, only the functions the host mounts.

| File | What it is |
|---|---|
| `host.ae` | The host: the mounted functions, the request policy, the spawn and the conversation |
| `chat.rexx` | The script: the conversation, file requests, and an `ADDRESS SYSTEM` attempt |
| `stuck.rexx` | A script that waits for a message the host never sends |
| `../lib/childbox/` | Shared host-side helpers: the conversation's replies and the audit log |

The host side of the worker protocol is RexxAE's `rexxworker` module
(`../RexxAE/worker/rexxworker`), built in with `--lib ../RexxAE/worker`.

## Run it

You need Aether 0.873.0 or later and a RexxAE checkout next to this repo
(`../RexxAE`). From the repo root:

```sh
make rexx
```

That builds RexxAE's worker and `build/rexx-host`, runs the conversation,
then runs `stuck.rexx`. The host's 5-second deadline sends a cancel, and the
worker stops the script:

```
worker: ERROR cancelled by host: deadline of 5000 ms passed
```

Contained runs need **Linux with Landlock**. Elsewhere the spawn refuses and
starts nothing; `make rexx UNCONTAINED=1` runs it with no process sandbox,
asked for by name. Decisions go to `build/audit.log`, including the
worker's own refusals.

## Use it in your own program

The worker runs **any** Rexx script; you don't change or rebuild it. In your
host:

1. **Launch the worker** with the grants it needs on its own (here: none,
   since the script comes over the socket):

   ```aether,fragment
   guest = sandbox.new("rexx-guest") {
   }
   w, err = rexxworker.launch(guest, prog, session, childbox.AUDIT_LOG)
   ```

2. **Mount the functions** your scripts may call, with their minimum and
   maximum argument counts, then send the script:

   ```aether,fragment
   rexxworker.mount(&w, "HOST_REQUEST", 1, 1)
   rexxworker.start(&w, source)
   ```

3. **Answer events** in a loop on `rexxworker.next(&w)`:
   - `CALL`: a mounted function was called. Answer it with
     `rexxworker.answer` (a JSON value), `grant_file` or `deny`.
   - `SAY`: the script printed something.
   - `DONE`: the script finished.

   `next` waits at most the deadline. When it passes, it sends a cancel and,
   if the worker doesn't answer, kills it.

Your scripts call the mounted functions like any Rexx function, for example
`LET cfg = HOST_REQUEST('config')`; `cfg.granted`, `cfg.text` and `cfg.why`
hold the answer. RexxAE's README has the full worker protocol ("Worker
process").
