# Needs Aether 0.878.0 or later (std.guest and std.guest.child first ship in
# 0.878.0; the floor is evidence-based). AE defaults to the
# ae on PATH, AETHER_HOME to your environment's (an installed release sets it).
# RexxAE's worker and the programs here are built by the same ae.
#
#   make python              the Python showcase, then a guest that goes quiet
#   make rexx                the RexxAE showcase, then a script stuck in HEAR
#   make js                  the JavaScript showcase (mquickjs-ae), then a
#                            script in while (true), a worker that never
#                            answers, and a frame over the cap
#   make js-test             js-worker's cases, contained on Linux
#   make ... UNCONTAINED=1   no process sandbox, asked for by name (macOS)
#
# js-worker is built by aeb (as mquickjs-ae is), in js/, with the engine
# compiled by the same Aether tree: AETHER_HOME. mquickjs-ae is the checkout
# js/mqjs points at (../mquickjs-ae).
AE ?= ae
AEB ?= aeb
REXXAE ?= ../RexxAE
WORKER := $(REXXAE)/build/worker/rexxae-worker
SILENT := $(REXXAE)/build/worker/silent
JS_WORKER := js/target/build/bin/js-worker
MODE := $(if $(filter 1,$(UNCONTAINED)),uncontained,)
# A sandboxed spawn finds libaether_sandbox.so under AETHER_HOME, which `ae run`
# sets but a program run from here would not have. Default it to the
# toolchain AE comes from (<prefix>/bin/ae, or a source tree's build/ae).
ifeq ($(strip $(AETHER_HOME)),)
AETHER_HOME := $(abspath $(dir $(shell command -v $(AE)))/..)
endif
export AETHER_HOME
# aeb runs `ae` from PATH: the AE named here, when it is a path.
AE_PATH := $(if $(findstring /,$(AE)),PATH=$(abspath $(dir $(AE))):$$PATH,)

.PHONY: all python rexx js js-test clean build-dir FORCE

all: python rexx js

python: build/python-host
	./build/python-host $(MODE)
	-./build/python-host stall $(MODE)

rexx: build/rexx-host $(WORKER)
	./build/rexx-host $(WORKER) $(MODE)
	-./build/rexx-host $(WORKER) stuck $(MODE)

js: build/js-host $(JS_WORKER) $(SILENT)
	./build/js-host $(JS_WORKER) $(MODE)
	-./build/js-host $(JS_WORKER) stuck $(MODE)
	-./build/js-host $(SILENT) deaf $(MODE)
	-./build/js-host $(JS_WORKER) big $(MODE)

js-test: build/js-test $(JS_WORKER) $(SILENT)
	./build/js-test

build/python-host: python/host.ae lib/childbox/module.ae | build-dir
	$(AE) build python/host.ae --lib lib -o $@

build/rexx-host: rexx/host.ae lib/childbox/module.ae | build-dir
	$(AE) build rexx/host.ae --lib lib -o $@

build/js-host: js/host.ae lib/childbox/module.ae | build-dir
	$(AE) build js/host.ae --lib lib -o $@

build/js-test: js/test_worker.ae | build-dir
	$(AE) build js/test_worker.ae -o $@

# mquickjs-ae's engine, the worker and the ROM generated for it (js/.build.ae).
$(JS_WORKER): FORCE
	cd js && $(AE_PATH) JS_AETHER_HOME=$(AETHER_HOME) $(AEB) .build.ae

# A worker that reads every frame, CANCEL included, and never answers:
# RexxAE's stand-in for one that has stopped (tests/worker_silent.ae).
$(SILENT): FORCE
	$(MAKE) -C $(REXXAE) build/worker/silent AE=$(AE)

# RexxAE's generic worker (its README, "Worker process").
$(WORKER): FORCE
	$(MAKE) -C $(REXXAE) worker AE=$(AE)

FORCE:

clean:
	rm -rf build js/target

build-dir:
	mkdir -p build
