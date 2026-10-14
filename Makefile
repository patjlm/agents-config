.PHONY: install check plugins

install:
	uv run install.py

check:
	uv run install.py --check

plugins:
	uv run install.py --plugins
