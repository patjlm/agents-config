.PHONY: install check plan plugins

# AGENT=<name> installs/validates a single agent (e.g. make install AGENT=pi).
# Filtered against valid names: stray AGENT values from the environment
# (e.g. AGENT=1 exported by other tooling) fall back to "all agents".
AGENT := $(if $(filter claude codex opencode omp pi,$(AGENT)),$(AGENT))

install:
	uv run install.py $(AGENT)

check:
	uv run install.py --check $(AGENT)

# Same as check, plus a unified diff of each file that would change — the
# quickest way to see how an MCP server renders into every agent's format.
plan:
	uv run install.py --plan $(AGENT)

plugins:
	uv run install.py --plugins
