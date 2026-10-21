.PHONY: install check plugins

# AGENT=<name> installs/validates a single agent (e.g. make install AGENT=pi).
# Filtered against valid names: stray AGENT values from the environment
# (e.g. AGENT=1 exported by other tooling) fall back to "all agents".
AGENT := $(if $(filter claude codex opencode omp pi,$(AGENT)),$(AGENT))

install:
	uv run install.py $(AGENT)

check:
	uv run install.py --check $(AGENT)

plugins:
	uv run install.py --plugins
