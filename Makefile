# Needs an Aether with sandbox.spawn_socket and std.ipc's frames (README,
# "Status"). Point AE and AETHER_HOME at one: RexxAE's worker and the
# programs here are built by the same ae.
#
#   make python              the Python showcase, then a guest that goes quiet
#   make rexx                the RexxAE showcase, then a script stuck in HEAR
#   make ... UNCONTAINED=1   no process sandbox, asked for by name (macOS)
AE ?= ae
REXXAE ?= ../RexxAE
WORKER := $(REXXAE)/build/worker/rexxae-worker
MODE := $(if $(filter 1,$(UNCONTAINED)),uncontained,)

.PHONY: all python rexx clean build-dir FORCE

all: python rexx

python: build/python-host
	./build/python-host $(MODE)
	-./build/python-host stall $(MODE)

rexx: build/rexx-host $(WORKER)
	./build/rexx-host $(WORKER) $(MODE)
	-./build/rexx-host $(WORKER) stuck $(MODE)

build/python-host: python/host.ae lib/childbox/module.ae | build-dir
	$(AE) build python/host.ae -o $@

build/rexx-host: rexx/host.ae lib/childbox/module.ae $(REXXAE)/worker/rexxworker/module.ae | build-dir
	$(AE) build rexx/host.ae --lib lib --lib $(REXXAE)/worker -o $@

# RexxAE's generic worker (its README, "Worker process").
$(WORKER): FORCE
	$(MAKE) -C $(REXXAE) worker AE=$(AE)

FORCE:

clean:
	rm -rf build

build-dir:
	mkdir -p build
