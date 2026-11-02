/* The RexxAE showcase's guest side of the conversation.
   Rexx here has no file, network or process access of its own: its only
   way out is three functions the host (rexx/host.ae) mounts in RexxAE's
   worker, each a message on the socket to the host:
     HOST_HEAR()          the host's next message
     HOST_TELL(line)      a message to the host
     HOST_REQUEST(name)   ask the host for a file: .granted, .text, .why
*/
DO FOREVER
    LET msg = HOST_HEAR()
    PARSE VAR msg verb rest

    IF verb = 'HELLO' THEN LET ok = HOST_TELL('HELLO from RexxAE')

    IF verb = 'ASK' THEN DO
        PARSE VAR rest op a b
        IF op = 'multiply' THEN LET ok = HOST_TELL('ANSWER' a * b)
        ELSE LET ok = HOST_TELL('ANSWER ?' op)

        /* The guest's own turn: it asks the host for things. */
        LET cfg = HOST_REQUEST('config')
        IF cfg.granted THEN LET ok = HOST_TELL('SAY config says:' TRIM(cfg.text))
        ELSE LET ok = HOST_TELL('SAY no config:' cfg.why)
        LET pw = HOST_REQUEST('/etc/passwd')
        IF pw.granted THEN LET ok = HOST_TELL('SAY read /etc/passwd through the host')
        ELSE LET ok = HOST_TELL('SAY the host said no:' pw.why)

        /* And it tries to help itself. */
        ADDRESS SYSTEM 'cat /etc/passwd'
        LET ok = HOST_TELL('PROBE ADDRESS SYSTEM cat /etc/passwd: RC' RC)
        LET ok = HOST_TELL('DONE')
    END

    IF verb = 'BYE' THEN DO
        LET ok = HOST_TELL('BYE')
        LEAVE
    END
END
EXIT 0
