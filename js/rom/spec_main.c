/* The JS worker's standard library, baked into ROM at build time the way
 * MicroQuickJS does it (no globals are added at run time from C).
 *
 * mquickjs-ae's own spec with is_example = 2: the language builtins plus
 * console.log, print and performance.now, and nothing that reaches the host
 * OS (no load, no gc, no timers). On top of it, one global of the worker's:
 *
 *   __host_call(name, argsJson)   a CALL frame to the host; returns the
 *                                 REPLY's JSON text, or throws
 *
 * The functions a host MOUNTs are JavaScript wrappers round it (worker.ae).
 * The spec builders are mquickjs-ae's (gen/genengine), used as a library. */
void *mqjs_stdlib_spec(int is_example);
void *stdlib(void);
void cfunc(void *ctx, const char *name, int length, const char *fn);
void cfunc_special(void *ctx, const char *name, int length, const char *proto, const char *fn);
void *build_global(void *spec);
int build_atoms(const char *stdlib_name, void *global_obj, void *c_function_decl, int argc, char **argv);

int main(int argc, char **argv)
{
    void *g = mqjs_stdlib_spec(2);
    cfunc(g, "__host_call", 2, "js_host_call");
    void *d = stdlib();
    cfunc_special(d, "bound", 0, "generic_params", "js_function_bound");
    return build_atoms("js_stdlib", build_global(g), build_global(d), argc, argv);
}
