# /// script
# requires-python = ">=3.11"
# dependencies = ["jinja2", "json5", "jsonschema"]
# ///
"""Render agent config templates, validate, and install them.

Usage:
    uv run install.py             # render + validate + install (incl. plugins)
    uv run install.py --check     # render + validate only, write nothing
    uv run install.py --plugins   # only plugins (incl. the mcp plugin)

Validation:
    - Rendered configs: syntax check (json5 parse). The upstream opencode
      schema (https://opencode.ai/config.json) is V1-stale — it rejects V2
      keys like "permissions"/"providers" — so only syntax is checked there.
      Revisit if upstream publishes a V2 schema.
    - Shared data (mcp/servers.jsonc): checked against mcp/servers.schema.json
      (self-owned, so it tracks reality).
    - claude/settings.jsonc: parse check (json5 — comments allowed) before
      rendering to ~/.claude/settings.json.

Install mechanisms (see AGENTS.md):
    - opencode: rendered template → ~/.config/opencode/opencode.jsonc.
    - claude: 1:1 files (CLAUDE.md, statusline, agents/, commands/, hooks/,
      skills/) → symlinks into ~/.claude. settings.jsonc is rendered to JSON
      and copied to ~/.claude/settings.json (Claude Code itself writes to it
      — permissions, /config — and those writes must not hit the repo; it
      also can't read comments). The unchanged check compares parsed JSON,
      so Claude Code's reformatting doesn't trigger reinstalls.
    - claude plugins: marketplaces (extraKnownMarketplaces) and plugins
      (enabledPlugins, true = install) live in claude/settings.json —
      install.py applies them via the `claude` CLI (idempotent).
    - claude MCP: mcp/servers.jsonc (opencode format) is translated into
      claude/plugins/mcp/.mcp.json — our own plugin, delivered via the
      patjlm-agents-config marketplace in claude/plugins/. Generated file,
      gitignored. Per-server "claude" overrides (deep-merged over the base
      server) handle claude-only differences — e.g. replacing a {file:...}
      secret reference with a ${VAR} env ref (claude cannot read files);
      secret contents are never inlined.
    - shared: agents/AGENTS.md (global instructions) → ~/.agents/AGENTS.md.

Idempotent: skips destinations whose content already matches the render.
Backs up differing destinations before overwriting.
"""
import json
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import jsonschema
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from json5 import loads as json5_loads

REPO = Path(__file__).resolve().parent
HOME = Path.home()

# Per-agent override keys in mcp/servers.jsonc — install-time directives,
# stripped before rendering into agent configs.
AGENT_OVERRIDE_KEYS = {"claude"}


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
    # Strip per-agent override keys ("claude", ...) — they are install-time
    # directives for install.py, not agent config.
    clean = {
        name: {k: v for k, v in server.items() if k not in AGENT_OVERRIDE_KEYS}
        for name, server in servers.items()
    }
    # Pre-rendered JSON block at 2-space indent to match config style.
    mcp_section = json.dumps({"servers": clean}, indent=2)
    return {"mcp_section": mcp_section, "home": str(HOME)}


def install(dest: Path, rendered: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Never write through a symlink (would hit the wrong target).
    if dest.is_symlink():
        dest.unlink()
        print(f"removed symlink: {dest}")
    if dest.exists() and dest.read_text() == rendered:
        print(f"unchanged: {dest}")
        return
    backup(dest)
    dest.write_text(rendered)
    dest.chmod(0o600)
    print(f"installed: {dest}")


def backup(dest: Path, backup_dir: Path | None = None) -> Path:
    """Copy dest to a timestamped .bak.

    Next to dest by default, or under backup_dir (mirroring the path under
    ~/.claude) when given — ~/.claude backups must not sit next to skills/
    commands, where Claude Code would pick them up as real content.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if backup_dir is None:
        backup_path = dest.with_name(f"{dest.name}.bak-{stamp}")
    else:
        backup_path = backup_dir / f"{dest.relative_to(CLAUDE_HOME)}.bak-{stamp}"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_dir() and not dest.is_symlink():
        shutil.copytree(dest, backup_path)
    else:
        shutil.copy2(dest, backup_path)
    print(f"backed up: {backup_path}")
    return backup_path


# --- claude/: 1:1 content symlinked into ~/.claude --------------------------

CLAUDE_SRC = REPO / "claude"
CLAUDE_HOME = HOME / ".claude"
CLAUDE_BACKUPS = CLAUDE_HOME / "backups"
CLAUDE_SETTINGS_SRC = CLAUDE_SRC / "settings.jsonc"


def load_claude_settings() -> dict:
    """Parse claude/settings.jsonc (json5 — comments allowed)."""
    return json5_loads(CLAUDE_SETTINGS_SRC.read_text())

# Our own local marketplace + plugin delivering the shared MCP server list.
# The marketplace lives in claude/plugins/ (declared in claude/settings.json
# as a directory source; its mcp plugin is enabled like any other plugin).
MCP_PLUGIN_TARGET = CLAUDE_SRC / "plugins" / "mcp" / ".mcp.json"


def claude_symlink_map() -> dict[Path, Path]:
    """dest in ~/.claude -> source file/dir in the repo.

    Top-level files and files under agents/commands/hooks are linked
    individually; skills are linked as whole directories (mirrors the
    ~/.agents skills pattern). settings.json is handled separately (copy).
    """
    links: dict[Path, Path] = {}
    for name in ("CLAUDE.md", "statusline-command.sh"):
        links[CLAUDE_HOME / name] = CLAUDE_SRC / name
    for sub in ("agents", "commands", "hooks"):
        for src in sorted((CLAUDE_SRC / sub).iterdir()):
            if src.is_file():
                links[CLAUDE_HOME / sub / src.name] = src
    for src in sorted((CLAUDE_SRC / "skills").iterdir()):
        if src.is_dir():
            links[CLAUDE_HOME / "skills" / src.name] = src
    return links


def install_symlink(
    dest: Path, source: Path, check_only: bool, backup_dir: Path | None = CLAUDE_BACKUPS
) -> None:
    """Link dest -> source. Backups go under backup_dir (mirroring the path
    under ~/.claude) or, when None, next to dest (for targets outside
    ~/.claude, where CLAUDE_BACKUPS cannot mirror the path)."""
    if dest.is_symlink() and dest.resolve() == source.resolve():
        print(f"unchanged: {dest}")
        return
    if check_only:
        print(f"would link: {dest} -> {source}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        dest.unlink()
        print(f"removed symlink: {dest}")
    elif dest.exists():
        backup(dest, backup_dir)
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    dest.symlink_to(source)
    print(f"linked: {dest} -> {source}")


def warn_settings_drift(
    dest_settings: dict, settings: dict, backup_path: Path | None
) -> None:
    """Claude Code writes to ~/.claude/settings.json (granted permissions
    land inside the existing "permissions" key, /config changes others).
    Warn which top-level keys differ before we overwrite them, pointing at
    the backup holding the previous content."""
    diff = sorted(
        k
        for k in set(dest_settings) | set(settings)
        if dest_settings.get(k) != settings.get(k)
    )
    if diff:
        where = (
            f"previous content in {backup_path}"
            if backup_path
            else "backed up before overwrite on install"
        )
        print(
            f"WARNING: ~/.claude/settings.json differs from "
            f"{CLAUDE_SETTINGS_SRC.relative_to(REPO)} in: {', '.join(diff)} — "
            f"overwritten ({where}); merge those changes into the source to keep them"
        )


def install_claude(check_only: bool) -> bool:
    """Install claude/ files into ~/.claude. Returns False on validation error."""
    try:
        settings = load_claude_settings()
    except ValueError as e:
        print(f"INVALID: {CLAUDE_SETTINGS_SRC} — {e}", file=sys.stderr)
        return False
    print(f"valid: {CLAUDE_SETTINGS_SRC}")
    for dest, source in claude_symlink_map().items():
        install_symlink(dest, source, check_only)
    # settings.json: rendered from settings.jsonc and copied, never
    # symlinked — Claude Code writes to it (permissions, /config).
    dest = CLAUDE_HOME / "settings.json"
    dest_settings = None
    if not dest.is_symlink() and dest.is_file():
        try:
            dest_settings = json.loads(dest.read_text())
        except ValueError:
            pass  # unparseable — treat as differing, backup + overwrite
    if dest_settings == settings:
        print(f"unchanged: {dest}")
        return True
    if check_only:
        if dest_settings is not None:
            warn_settings_drift(dest_settings, settings, None)
        print(f"would install: {dest}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if dest.is_symlink():
        dest.unlink()
        print(f"removed symlink: {dest}")
    elif dest.exists():
        backup_path = backup(dest, CLAUDE_BACKUPS)
    if dest_settings is not None:
        warn_settings_drift(dest_settings, settings, backup_path)
    dest.write_text(json.dumps(settings, indent=2) + "\n")
    dest.chmod(0o600)
    print(f"installed: {dest}")
    return True


# --- agents/: shared content, symlinked into ~/.agents -----------------------

AGENTS_SRC = REPO / "agents"


def install_shared(check_only: bool) -> None:
    """Cross-agent 1:1 content. The ~/.agents dir itself is managed by other
    tooling (skills, .skill-lock.json) — only AGENTS.md is ours."""
    install_symlink(
        HOME / ".agents" / "AGENTS.md",
        AGENTS_SRC / "AGENTS.md",
        check_only,
        backup_dir=None,  # not under ~/.claude — backup next to dest
    )


# --- claude plugins: driven by claude/settings.json ---------------------------
# Marketplaces (extraKnownMarketplaces) and plugins (enabledPlugins, true =
# install) both live in claude/settings.json — single source of truth.


def load_enabled_plugins() -> list[str]:
    """Plugins to install: enabledPlugins=true in claude/settings.jsonc —
    the same list Claude Code uses to enable/disable."""
    enabled = load_claude_settings().get("enabledPlugins", {})
    if not isinstance(enabled, dict) or not all(
        isinstance(v, bool) for v in enabled.values()
    ):
        raise ValueError('"enabledPlugins" must map plugin ids to booleans')
    return [plugin for plugin, on in enabled.items() if on]


def load_marketplaces() -> list[str]:
    """Marketplaces declared in claude/settings.jsonc (extraKnownMarketplaces)
    — the same place the claude CLI declares them. Returns add-able sources:
    github repos or local directory paths."""
    marketplaces = load_claude_settings().get("extraKnownMarketplaces", {})
    if not isinstance(marketplaces, dict):
        raise ValueError('"extraKnownMarketplaces" must be an object')
    sources = []
    for name, entry in marketplaces.items():
        src = entry.get("source", {}) if isinstance(entry, dict) else {}
        kind = src.get("source")
        if kind == "github" and isinstance(src.get("repo"), str):
            sources.append(src["repo"])
        elif kind == "directory" and isinstance(src.get("path"), str):
            sources.append(src["path"])
        else:
            raise ValueError(
                f'marketplace "{name}" must declare a github repo or directory path'
            )
    return sources


def run_claude(args: list[str]) -> bool:
    result = subprocess.run(["claude", *args], capture_output=True, text=True)
    print((result.stdout + result.stderr).strip())
    if result.returncode != 0:
        print(f"FAILED: claude {' '.join(args)}", file=sys.stderr)
        return False
    return True


def check_plugin_drift() -> None:
    """Warn about user-scope plugins installed on this machine but not
    managed by the repo (e.g. installed manually via the CLI) — they would
    be lost on a fresh machine until added to claude/settings.json."""
    installed_path = CLAUDE_HOME / "plugins" / "installed_plugins.json"
    try:
        installed = json.loads(installed_path.read_text()).get("plugins", {})
    except (OSError, ValueError):
        return  # nothing installed yet (fresh machine)
    managed = set(load_claude_settings().get("enabledPlugins", {}))
    for plugin, scopes in installed.items():
        if any(s.get("scope") == "user" for s in scopes) and plugin not in managed:
            print(
                f"WARNING: {plugin} is installed (user scope) but not in "
                "claude/settings.json enabledPlugins — add it there to keep it managed"
            )


def remove_mcp_duplicates() -> bool:
    """User-scope MCP servers in ~/.claude.json that our mcp plugin now
    manages (e.g. a pre-existing slack-mcp entry) would show up as
    duplicates — remove them."""
    try:
        managed = json.loads(MCP_PLUGIN_TARGET.read_text())["mcpServers"]
    except (OSError, ValueError, KeyError):
        return True  # nothing rendered (fresh machine, check mode)
    try:
        live = json.loads((HOME / ".claude.json").read_text()).get("mcpServers", {})
    except (OSError, ValueError):
        return True
    ok = True
    for name in managed:
        if name in live:
            ok &= run_claude(["mcp", "remove", name, "--scope", "user"])
    return ok


def install_plugins(check_only: bool) -> bool:
    """Add marketplaces + install plugins, both driven by claude/settings.json
    (extraKnownMarketplaces / enabledPlugins). The claude CLI commands are
    idempotent no-ops when already present."""
    try:
        plugins = load_enabled_plugins()
        marketplaces = load_marketplaces()
    except ValueError as e:
        print(f"INVALID: {CLAUDE_SETTINGS_SRC} — {e}", file=sys.stderr)
        return False
    print(f"valid: {CLAUDE_SETTINGS_SRC} ({len(plugins)} plugins, "
          f"{len(marketplaces)} marketplaces)")
    if check_only:
        check_plugin_drift()
        return True
    ok = True
    for source in marketplaces:
        ok &= run_claude(["plugin", "marketplace", "add", source])
    for plugin in plugins:
        ok &= run_claude(["plugin", "install", plugin])
    ok &= remove_mcp_duplicates()
    check_plugin_drift()
    return ok


# --- mcp/servers.jsonc -> claude/mcp/.mcp.json (our "mcp" plugin) ------------

# Per-agent overrides: a key like "claude" on a server is deep-merged over
# the base server when rendering for that agent (dicts merge per-key,
# scalars/lists replace); "exclude": true inside it skips the server for
# that agent. Base values stay in opencode syntax; override values are
# written in the target agent's syntax, so a claude override can e.g.
# replace a {file:...} secret reference with a ${VAR} env ref.


def deep_merge(base: dict, override: dict) -> dict:
    """dicts merge per-key; scalars and lists replace wholesale."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def for_agent(server: dict, agent: str) -> dict | None:
    """Server as rendered for agent, overrides applied. None = skip
    (disabled, or excluded via the agent's override block)."""
    override = server.get(agent, {})
    if server.get("disabled") or override.get("exclude"):
        return None
    base = {k: v for k, v in server.items() if k not in AGENT_OVERRIDE_KEYS}
    override = {k: v for k, v in override.items() if k != "exclude"}
    return deep_merge(base, override)


def expand_placeholder(value: str) -> str:
    """Translate opencode placeholders to claude .mcp.json syntax.

    {env:VAR} -> ${VAR} (expanded by Claude Code at runtime). {file:PATH}
    is an error: claude cannot read secret files — override the value in
    the server's "claude" block instead.
    """

    def sub(m: re.Match) -> str:
        if m.group(1) == "file":
            raise ValueError(
                f"{m.group(0)} in {value!r}: claude cannot read secret "
                'files — override this value in the "claude" block'
            )
        return "${" + m.group(2) + "}"

    return re.sub(r"\{(env|file):([^}]+)\}", sub, value)


def translate_server(server: dict) -> dict:
    """opencode-format server (claude overrides already applied) ->
    claude .mcp.json entry.

    Notes: opencode per-server "timeout" has no claude equivalent (global
    MCP_TIMEOUT/MCP_TOOL_TIMEOUT env vars instead) and is dropped; "cwd"
    is wrapped in a bash -c since claude stdio has no cwd key.
    """
    if server["type"] == "remote":
        out = {"type": "http", "url": server["url"]}
        headers = {
            k: expand_placeholder(v) for k, v in server.get("headers", {}).items()
        }
        if headers:
            out["headers"] = headers
        return out
    command = server["command"]
    if server.get("cwd"):
        cwd = shlex.quote(server["cwd"])
        joined = " ".join(shlex.quote(c) for c in command)
        command = ["bash", "-c", f"cd {cwd} && exec {joined}"]
    out = {"type": "stdio", "command": command[0], "args": command[1:]}
    if server.get("environment"):
        out["env"] = {
            k: expand_placeholder(v) for k, v in server["environment"].items()
        }
    return out


def mcp_json(servers: dict) -> str:
    entries = {}
    for name, server in servers.items():
        merged = for_agent(server, "claude")
        if merged is not None:
            entries[name] = translate_server(merged)
    return json.dumps({"mcpServers": entries}, indent=2) + "\n"


def render_mcp_plugin(servers: dict, check_only: bool) -> bool:
    """Render claude/plugins/mcp/.mcp.json from the shared server list.

    Generated file — gitignored, never hand-edited (edit mcp/servers.jsonc
    + re-run install). Per-server claude overrides are applied (see
    for_agent); secret contents are never inlined at render time.
    Installing/enabling the plugin is handled by install_plugins
    via enabledPlugins, like any other plugin."""
    try:
        rendered = mcp_json(servers)
        json.loads(rendered)  # sanity: always valid JSON
    except (ValueError, OSError) as e:
        print(f"INVALID: mcp/servers.jsonc (claude rendering) — {e}", file=sys.stderr)
        return False
    dest = MCP_PLUGIN_TARGET
    if check_only:
        print(f"valid: {dest} ({len(json.loads(rendered)['mcpServers'])} servers)")
        return True
    if dest.is_file() and dest.read_text() == rendered:
        print(f"unchanged: {dest}")
    else:
        dest.write_text(rendered)
        print(f"rendered: {dest}")
    return True


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    plugins_only = "--plugins" in sys.argv[1:]
    if plugins_only:
        try:
            servers = load_mcp_servers()
        except (ValueError, jsonschema.ValidationError) as e:
            print(f"INVALID: mcp/servers.jsonc — {e}", file=sys.stderr)
            return 1
        ok = render_mcp_plugin(servers, check_only)
        ok &= install_plugins(check_only)
        return 0 if ok else 1
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
    install_shared(check_only)
    if not install_claude(check_only):
        failed = True
    if not render_mcp_plugin(servers, check_only):
        failed = True
    if not install_plugins(check_only):
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
