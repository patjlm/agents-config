# /// script
# requires-python = ">=3.11"
# dependencies = ["jinja2", "json5", "jsonschema"]
# ///
"""Render agent config templates, validate, and install them.

Usage:
    uv run install.py             # render + validate + install (all agents)
    uv run install.py --check     # render + validate only, write nothing
    uv run install.py --plugins   # only claude plugins (incl. the mcp plugin)

Structure: shared primitives, shared data, then one section per agent —
INSTALLERS lists them, so adding an agent is a new section + one entry.
Agent-specific *formats* live in the agent's folder as Jinja templates
(opencode.jsonc.j2, settings.jsonc.j2); agent-specific *behavior* (symlinks,
CLI calls, drift checks) lives in the agent's section here.

Validation:
    - Rendered configs: syntax check (json5 parse). The upstream opencode
      schema (https://opencode.ai/config.json) is V1-stale — it rejects V2
      keys like "permissions"/"providers" — so only syntax is checked there.
      Revisit if upstream publishes a V2 schema.
    - Shared data (mcp/servers.jsonc): checked against mcp/servers.schema.json
      (self-owned, so it tracks reality).
    - claude/settings.jsonc.j2: parse check (json5 — comments allowed) before
      rendering to ~/.claude/settings.json.

Install mechanisms (see AGENTS.md):
    - opencode: template render → ~/.config/opencode/opencode.jsonc.
    - claude: 1:1 files (CLAUDE.md, statusline, agents/, commands/, hooks/,
      skills/) → symlinks into ~/.claude. settings.jsonc.j2 is rendered to
      JSON and copied to ~/.claude/settings.json — never symlinked, Claude
      Code itself writes to it (permissions, /config); the unchanged check
      compares parsed JSON so its reformatting doesn't trigger reinstalls,
      and differing keys warn with the backup path. Plugins and marketplaces
      are driven by the same settings file and applied via the claude CLI.
      mcp/servers.jsonc is translated into claude/plugins/mcp/.mcp.json (our
      own plugin under the patjlm-agents-config marketplace, gitignored;
      secrets never inlined).
    - shared: agents/AGENTS.md (global instructions) → ~/.agents/AGENTS.md.

Idempotent: skips destinations whose content already matches the render,
backs up differing destinations before overwriting, never writes through
symlinks. One agent failing does not abort the others.
"""
import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from json5 import loads as json5_loads

REPO = Path(__file__).resolve().parent
HOME = Path.home()

JINJA = Environment(
    loader=FileSystemLoader(str(REPO)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


# --- shared primitives: backup / link / write --------------------------------


def backup(dest: Path, root: Path | None = None) -> Path:
    """Copy dest to a timestamped .bak.

    Next to dest by default, or mirroring the path under root/"backups"
    when root is given — ~/.claude backups must not sit next to skills/
    commands, where Claude Code would pick them up as real content.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if root is None:
        backup_path = dest.with_name(f"{dest.name}.bak-{stamp}")
    else:
        backup_path = root / "backups" / f"{dest.relative_to(root)}.bak-{stamp}"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_dir() and not dest.is_symlink():
        shutil.copytree(dest, backup_path)
    else:
        shutil.copy2(dest, backup_path)
    print(f"backed up: {backup_path}")
    return backup_path


def link(dest: Path, source: Path, check_only: bool, root: Path | None = None) -> None:
    """Symlink dest -> source (backups under root/"backups", see backup)."""
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
        backup(dest, root)
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    dest.symlink_to(source)
    print(f"linked: {dest} -> {source}")


def write_if_changed(
    dest: Path,
    text: str,
    check_only: bool,
    *,
    root: Path | None = None,
    parse: Callable[[str], Any] | None = None,
    warn: Callable[[Any, Path | None], None] | None = None,
    keep_backup: bool = True,
) -> bool:
    """Write text to dest unless it is already there. True = dest changes
    (or would, in check mode).

    Safety rules: never write through a symlink (unlink first), back up
    differing destinations before overwriting (next to dest, or mirroring
    under root/"backups" when root is given), chmod 0600. With parse, the
    unchanged check compares parsed content (formatting-insensitive) and
    warn(old, backup_path) — if given — runs before an overwrite, for drift
    warnings (backup_path is None in check mode). keep_backup=False for
    generated artifacts whose previous content is derivable from tracked
    sources (e.g. the rendered .mcp.json).
    """
    old = None
    same = False
    if not dest.is_symlink() and dest.is_file():
        raw = dest.read_text()
        if parse is None:
            same = raw == text
        else:
            try:
                old = parse(raw)
            except ValueError:
                old = None  # unparseable — treat as differing
            same = old is not None and old == parse(text)
    if same:
        print(f"unchanged: {dest}")
        return False
    if check_only:
        if warn is not None and old is not None:
            warn(old, None)
        print(f"would install: {dest}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if dest.is_symlink():
        dest.unlink()
        print(f"removed symlink: {dest}")
    elif dest.exists() and keep_backup:
        backup_path = backup(dest, root)
    if warn is not None and old is not None:
        warn(old, backup_path)
    dest.write_text(text)
    dest.chmod(0o600)
    print(f"installed: {dest}")
    return True


# --- shared data: mcp/ -------------------------------------------------------


# Per-agent override keys in mcp/servers.jsonc — must match the override
# properties allowed by mcp/servers.schema.json. A block named after an
# agent is deep-merged over the base server when rendering for that agent
# (see for_agent); stripped from the base for everyone else.
AGENTS = ("claude", "codex", "opencode")


def load_mcp_servers() -> dict:
    """Load and schema-check the canonical MCP server list."""
    with open(REPO / "mcp" / "servers.jsonc") as f:
        servers = json5_loads(f.read())
    with open(REPO / "mcp" / "servers.schema.json") as f:
        schema = json.load(f)
    jsonschema.validate(instance=servers, schema=schema)
    return servers


def deep_merge(base: dict, override: dict) -> dict:
    """dicts merge per-key; scalars and lists replace wholesale."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def for_agent(server: dict, agent: str, *, keep_disabled: bool = False) -> dict | None:
    """Server as rendered for agent, its override block applied. None = skip
    (excluded via the override block, or disabled — unless keep_disabled,
    for agents that understand the "disabled" key natively)."""
    override = server.get(agent, {})
    if (server.get("disabled") and not keep_disabled) or override.get("exclude"):
        return None
    base = {k: v for k, v in server.items() if k not in AGENTS}
    override = {k: v for k, v in override.items() if k != "exclude"}
    return deep_merge(base, override)


# --- shared: agents/ ---------------------------------------------------------


AGENTS_SRC = REPO / "agents"


def install_shared(servers: dict, check_only: bool) -> bool:
    """Cross-agent 1:1 content. The ~/.agents dir itself is managed by other
    tooling (skills, .skill-lock.json) — only AGENTS.md is ours."""
    link(HOME / ".agents" / "AGENTS.md", AGENTS_SRC / "AGENTS.md", check_only)
    return True


# --- opencode ----------------------------------------------------------------


OPENCODE_DEST = HOME / ".config/opencode/opencode.jsonc"


def install_opencode(servers: dict, check_only: bool) -> bool:
    """Render opencode/opencode.jsonc.j2 → ~/.config/opencode/opencode.jsonc."""
    clean = {}
    for name, server in servers.items():
        rendered = for_agent(server, "opencode", keep_disabled=True)
        if rendered is not None:
            clean[name] = rendered
    rendered = JINJA.get_template("opencode/opencode.jsonc.j2").render(
        mcp_section=json.dumps({"servers": clean}, indent=2), home=str(HOME)
    )
    try:
        json5_loads(rendered)  # syntax check (schema is V1-stale, see AGENTS.md)
    except ValueError as e:
        print(f"INVALID: {OPENCODE_DEST} — {e}", file=sys.stderr)
        return False
    print(f"valid: {OPENCODE_DEST}")
    write_if_changed(OPENCODE_DEST, rendered, check_only)
    return True


# --- claude ------------------------------------------------------------------


CLAUDE_SRC = REPO / "claude"
CLAUDE_HOME = HOME / ".claude"

# Our own local marketplace + plugin delivering the shared MCP server list.
# The marketplace lives in claude/plugins/ (declared in
# claude/settings.jsonc.j2 as a directory source; its mcp plugin is enabled
# like any other plugin).
MCP_PLUGIN_TARGET = CLAUDE_SRC / "plugins" / "mcp" / ".mcp.json"


def load_claude_settings() -> dict:
    """Render claude/settings.jsonc.j2 (comments + {{ home }}/{{ repo }}
    substitution) and parse it (json5)."""
    rendered = JINJA.get_template("claude/settings.jsonc.j2").render(
        home=str(HOME), repo=str(REPO)
    )
    return json5_loads(rendered)


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
            f"claude/settings.jsonc.j2 in: {', '.join(diff)} — "
            f"overwritten ({where}); merge those changes into the source to keep them"
        )


def install_claude(servers: dict, check_only: bool) -> bool:
    """Install claude/ files into ~/.claude, then plugins (incl. the mcp
    plugin). Returns False on validation error."""
    try:
        settings = load_claude_settings()
    except ValueError as e:
        print(f"INVALID: claude/settings.jsonc.j2 — {e}", file=sys.stderr)
        return False
    print("valid: claude/settings.jsonc.j2")
    for dest, source in claude_symlink_map().items():
        link(dest, source, check_only, root=CLAUDE_HOME)

    # settings.json: rendered from settings.jsonc.j2 and copied, never
    # symlinked — Claude Code writes to it (permissions, /config).

    def drift_warn(old: dict, backup_path: Path | None) -> None:
        warn_settings_drift(old, settings, backup_path)

    write_if_changed(
        CLAUDE_HOME / "settings.json",
        json.dumps(settings, indent=2) + "\n",
        check_only,
        root=CLAUDE_HOME,
        parse=json.loads,
        warn=drift_warn,
    )
    return install_claude_plugins(servers, check_only)


# --- claude plugins: driven by claude/settings.jsonc.j2 ----------------------


def load_enabled_plugins() -> list[str]:
    """Plugins to install: enabledPlugins=true in the settings template —
    the same list Claude Code uses to enable/disable."""
    enabled = load_claude_settings().get("enabledPlugins", {})
    if not isinstance(enabled, dict) or not all(
        isinstance(v, bool) for v in enabled.values()
    ):
        raise ValueError('"enabledPlugins" must map plugin ids to booleans')
    return [plugin for plugin, on in enabled.items() if on]


def load_marketplaces() -> list[str]:
    """Marketplaces declared in the settings template (extraKnownMarketplaces)
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
    be lost on a fresh machine until added to the settings template."""
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
                "claude/settings.jsonc.j2 enabledPlugins — add it there to keep it managed"
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


def apply_plugins(check_only: bool) -> bool:
    """Add marketplaces + install plugins, both driven by the settings
    template (extraKnownMarketplaces / enabledPlugins). The claude CLI
    commands are idempotent no-ops when already present."""
    try:
        plugins = load_enabled_plugins()
        marketplaces = load_marketplaces()
    except ValueError as e:
        print(f"INVALID: claude/settings.jsonc.j2 — {e}", file=sys.stderr)
        return False
    print(f"valid: claude/settings.jsonc.j2 ({len(plugins)} plugins, "
          f"{len(marketplaces)} marketplaces)")
    if check_only:
        check_plugin_drift()
        return True
    if shutil.which("claude") is None:
        print("skipping claude plugins: claude CLI not found on PATH")
        return True
    ok = True
    for source in marketplaces:
        ok &= run_claude(["plugin", "marketplace", "add", source])
    for plugin in plugins:
        ok &= run_claude(["plugin", "install", plugin])
    ok &= remove_mcp_duplicates()
    check_plugin_drift()
    return ok


# --- claude mcp: mcp/servers.jsonc -> claude/plugins/mcp/.mcp.json -----------


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
    Installing/enabling the plugin is handled by apply_plugins
    via enabledPlugins, like any other plugin."""
    try:
        rendered = mcp_json(servers)
    except ValueError as e:
        print(f"INVALID: mcp/servers.jsonc (claude rendering) — {e}", file=sys.stderr)
        return False
    write_if_changed(MCP_PLUGIN_TARGET, rendered, check_only, keep_backup=False)
    return True


def install_claude_plugins(servers: dict, check_only: bool) -> bool:
    """The mcp plugin render + plugin application (--plugins shortcut)."""
    ok = render_mcp_plugin(servers, check_only)
    ok &= apply_plugins(check_only)
    return ok


# --- main --------------------------------------------------------------------


INSTALLERS = (install_shared, install_opencode, install_claude)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render agent config templates, validate, and install them."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="render + validate only, write nothing",
    )
    parser.add_argument(
        "--plugins",
        action="store_true",
        help="only claude plugins (incl. the mcp plugin)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        servers = load_mcp_servers()
    except (ValueError, jsonschema.ValidationError) as e:
        print(f"INVALID: mcp/servers.jsonc — {e}", file=sys.stderr)
        return 1
    print("valid: mcp/servers.jsonc")
    if args.plugins:
        return 0 if install_claude_plugins(servers, args.check) else 1
    ok = True
    for installer in INSTALLERS:
        try:
            ok &= installer(servers, args.check)
        except Exception as e:  # one agent failing must not abort the others
            ok = False
            print(f"FAILED: {installer.__name__} — {e!r}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
