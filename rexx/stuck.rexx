/* A guest that waits for a message the host never sends: the hang Adil
   pointed out on aether#3503. HOST_HEAR blocks the worker on the socket;
   the host's deadline sends CANCEL and the worker stops the script with
   rexxae_cancel, so the host is never left waiting. */
LET msg = HOST_HEAR()
LET ok = HOST_TELL('SAY waiting for a second message, which never comes')
LET more = HOST_HEAR()
SAY 'never reached:' more
