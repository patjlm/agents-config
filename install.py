# /// script
# requires-python = ">=3.11"
# dependencies = ["jinja2", "json5", "jsonschema"]
# ///
"""Render agent config templates, validate, and install them.

Usage:
    uv run install.py           # render + validate + install
    uv run install.py --check   # render + validate only, write nothing

Validation:
    - Rendered configs: syntax check (json5 parse). The upstream opencode
      schema (https://opencode.ai/config.json) is V1-stale — it rejects V2
      keys like "permissions"/"providers" — so only syntax is checked there.
      Revisit if upstream publishes a V2 schema.
    - Shared data (mcp/servers.jsonc): checked against mcp/servers.schema.json
      (self-owned, so it tracks reality).

Idempotent: skips destinations whose content already matches the render.
Backs up differing destinations before overwriting.
"""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import jsonschema
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from json5 import loads as json5_loads

REPO = Path(__file__).resolve().parent
HOME = Path.home()


def load_mcp_servers() -> dict:
    """Load and schema-check the canonical MCP server list."""
    with open(REPO / "mcp" / "servers.jsonc") as f:
        servers = json5_loads(f.read())
    with open(REPO / "mcp" / "servers.schema.json") as f:
        schema = json.load(f)
    jsonschema.validate(instance=servers, schema=schema)
    return servers


def validate_opencode(rendered: str) -> None:
    """Syntax-check a rendered opencode.jsonc (JSONC parse)."""
    json5_loads(rendered)  # raises on syntax errors


# destination -> {template, validate}
TARGETS = {
    HOME / ".config/opencode/opencode.jsonc": {
        "template": "opencode/opencode.jsonc.j2",
        "validate": validate_opencode,
    },
}


def build_context(servers: dict) -> dict:
    """Shared data injected into every template."""
    # Pre-rendered JSON block at 2-space indent to match config style.
    mcp_section = json.dumps({"servers": servers}, indent=2)
    return {"mcp_section": mcp_section}


def install(dest: Path, rendered: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Never write through a symlink (would hit the wrong target).
    if dest.is_symlink():
        dest.unlink()
        print(f"removed symlink: {dest}")
    if dest.exists() and dest.read_text() == rendered:
        print(f"unchanged: {dest}")
        return
    if dest.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = dest.with_name(f"{dest.name}.bak-{stamp}")
        shutil.copy2(dest, backup)
        print(f"backed up: {backup}")
    dest.write_text(rendered)
    dest.chmod(0o600)
    print(f"installed: {dest}")


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    try:
        servers = load_mcp_servers()
    except (ValueError, jsonschema.ValidationError) as e:
        print(f"INVALID: mcp/servers.jsonc — {e}", file=sys.stderr)
        return 1
    print("valid: mcp/servers.jsonc")
    env = Environment(
        loader=FileSystemLoader(str(REPO)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    ctx = build_context(servers)
    failed = False
    for dest, spec in TARGETS.items():
        rendered = env.get_template(spec["template"]).render(**ctx)
        try:
            spec["validate"](rendered)
        except ValueError as e:
            failed = True
            print(f"INVALID: {dest} — {e}", file=sys.stderr)
            continue
        print(f"valid: {dest}")
        if not check_only:
            install(dest, rendered)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
