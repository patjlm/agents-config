.PHONY: install check

install:
	uv run install.py

check:
	uv run install.py --check
