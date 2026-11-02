// A guest that says more than a frame may hold (64 KiB): the host refuses
// the frame before buffering it, hangs up and kills the worker.
hostHear();
print('x'.repeat(70000));
