/* The C the JS worker cannot avoid: the ROM table MicroQuickJS bakes at
 * build time, which names its host functions as C symbols, and a few
 * one-line wrappers for what Aether cannot spell (a function pointer, a
 * printf-style throw). Everything else is worker.ae. */
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/syscall.h>
#endif
#include "mquickjs.h"

/* worker.ae: the host functions the ROM below names. */
JSValue js_print(JSContext *ctx, JSValue *this_val, int argc, JSValue *argv);
JSValue js_host_call(JSContext *ctx, JSValue *this_val, int argc, JSValue *argv);
JSValue js_date_constructor(JSContext *ctx, JSValue *this_val, int argc, JSValue *argv);
JSValue js_date_now(JSContext *ctx, JSValue *this_val, int argc, JSValue *argv);
JSValue js_performance_now(JSContext *ctx, JSValue *this_val, int argc, JSValue *argv);
/* worker.ae: polls the socket for CANCEL; nonzero stops the script. */
int jsw_interrupt(JSContext *ctx, void *opaque);

#include "jsworker_stdlib.h"

const void *jsw_stdlib(void) { return &js_stdlib; }

/* MicroQuickJS calls the handler every JS_INTERRUPT_COUNTER_INIT (10000)
 * function calls and backward jumps, and in the regexp engine; a nonzero
 * return throws an uncatchable InternalError("interrupted"). */
void jsw_install_interrupt(JSContext *ctx) { JS_SetInterruptHandler(ctx, jsw_interrupt); }

JSValue jsw_throw(JSContext *ctx, const char *msg)
{
    return JS_ThrowError(ctx, JS_CLASS_ERROR, "%s", msg);
}

/* PROBE: open a file round std and its sandbox checks, as a bug in the
 * engine could. On Linux (x86_64, arm64) the openat system call made with
 * the instruction itself, round libc and so round the LD_PRELOAD layer too
 * (which refuses libc's syscall()): only the kernel layer can answer it.
 * Elsewhere libc's open. 0 when it opened, else errno. */
#if defined(__linux__) && (defined(__x86_64__) || defined(__aarch64__))
#define JSW_RAW_OPENAT 1
static long raw_openat(const char *path)
{
    long ret;
#if defined(__x86_64__)
    register long r10 __asm__("r10") = 0;
    __asm__ volatile ("syscall"
                      : "=a"(ret)
                      : "a"((long)SYS_openat), "D"((long)AT_FDCWD), "S"(path), "d"((long)O_RDONLY), "r"(r10)
                      : "rcx", "r11", "memory");
#else
    register long x8 __asm__("x8") = SYS_openat;
    register long x0 __asm__("x0") = AT_FDCWD;
    register long x1 __asm__("x1") = (long)path;
    register long x2 __asm__("x2") = O_RDONLY;
    register long x3 __asm__("x3") = 0;
    __asm__ volatile ("svc #0"
                      : "+r"(x0)
                      : "r"(x8), "r"(x1), "r"(x2), "r"(x3)
                      : "memory");
    ret = x0;
#endif
    return ret;
}
#endif

const char *jsw_raw_open_kind(void)
{
#ifdef JSW_RAW_OPENAT
    return "raw syscall instruction openat";
#else
    return "libc open";
#endif
}

int jsw_raw_open(const char *path)
{
#ifdef JSW_RAW_OPENAT
    long fd = raw_openat(path);
    if (fd < 0)
        return (int)-fd;
#else
    int fd = open(path, O_RDONLY);
    if (fd < 0)
        return errno;
#endif
    close((int)fd);
    return 0;
}

const char *jsw_strerror(int e) { return strerror(e); }
