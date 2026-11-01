# Both showcases share one Aether: the RexxAE library and the programs here
# must be built by the same ae, or the shared runtime refuses to link.
# Point AETHER_HOME and AE at one release (README, "Run it").
AE ?= ae
REXXAE ?= ../RexxAE

.PHONY: all python rexx clean

all: python rexx

python:
	$(AE) run python/host.ae

rexx: build/rexx-guest
	$(AE) run rexx/host.ae

build/rexx-guest: rexx/guest.ae lib/childbox/module.ae $(REXXAE)/build/aether
	mkdir -p build
	$(AE) build rexx/guest.ae --lib lib --lib $(REXXAE)/build/aether -o $@

$(REXXAE)/build/aether:
	$(MAKE) -C $(REXXAE) aether-lib AE=$(AE)

clean:
	rm -rf build
