# /// script
# requires-python = ">=3.11"
# dependencies = ["jinja2", "json5", "jsonschema", "pyyaml"]
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
    - claude: 1:1 files (statusline, agents/, commands/, hooks/,
      skills/) → symlinks into ~/.claude. settings.jsonc.j2 is rendered to
      JSON and copied to ~/.claude/settings.json — never symlinked, Claude
      Code itself writes to it (permissions, /config); the unchanged check
      compares parsed JSON so its reformatting doesn't trigger reinstalls,
      and differing keys warn with the backup path. Plugins and marketplaces
      are driven by the same settings file and applied via the claude CLI.
      mcp/servers.jsonc is translated into claude/plugins/mcp/.mcp.json (our
      own plugin under the patjlm-agents-config marketplace, gitignored;
      secrets never inlined).
    - instructions: agents/AGENTS.md (common guidelines) + optional
      <agent>/AGENTS.md (agent-specific additions) → combined and written
      to each agent's global instructions file (~/.claude/CLAUDE.md,
      ~/.codex/AGENTS.md, ~/.config/opencode/AGENTS.md,
      ~/.omp/agent/AGENTS.md) — generated, not symlinked, so hand edits
      surface as drift (backups kept).
      ~/.agents/AGENTS.md stays a symlink to the common file (convention
      anchor; no agent reads it for instructions).
    - config.jsonc: repo-wide settings (enmaas.enabled), rendered into every
      agent config — flip + re-run to switch agents between EnMaaS and their
      fallbacks (claude: Vertex via gcloud ADC, opencode: openai, codex:
      default OpenAI auth).
    - codex: config.toml.j2 (+ per-model profile files) → ~/.codex/config.toml
      (drift-aware — `codex mcp add` writes there), AGENTS.md generation
      (see instructions), mcp/servers.jsonc → [mcp_servers.*] tables.
    - omp: omp/models.yml.j2 → ~/.omp/agent/models.yml (EnMaaS providers,
      apiKey read via !cat from the secret file at request time — command
      form, no env vars), mcp/servers.jsonc → ~/.omp/agent/mcp.json
      (placeholders become !commands, resolved by omp at connect time),
      AGENTS.md generation (see instructions — without it omp falls back
      to reading ~/.claude/CLAUDE.md), and a surgical modelRoles.default
      edit in omp-owned config.yml (config.yml is written by omp itself,
      /settings and `omp config set`, so it is never rendered wholesale).

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
import tomllib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema
import yaml
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


def warn_drift(
    dest_label: str, source_label: str, old: dict, new: dict, backup_path: Path | None
) -> None:
    """The agent itself writes to its installed config (Claude Code:
    permissions, /config; codex: `codex mcp add`). Warn which top-level
    keys differ before we overwrite them, pointing at the backup holding
    the previous content."""
    diff = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    if not diff:
        return
    where = (
        f"previous content in {backup_path}"
        if backup_path
        else "backed up before overwrite on install"
    )
    print(
        f"WARNING: {dest_label} differs from {source_label} in: "
        f"{', '.join(diff)} — overwritten ({where}); merge those changes "
        "into the source to keep them"
    )


# --- shared data: mcp/ -------------------------------------------------------


# Per-agent override keys in mcp/servers.jsonc — must match the override
# properties allowed by mcp/servers.schema.json. A block named after an
# agent is deep-merged over the base server when rendering for that agent
# (see for_agent); stripped from the base for everyone else.
AGENTS = ("claude", "codex", "opencode", "omp")


def load_mcp_servers() -> dict:
    """Load and schema-check the canonical MCP server list."""
    with open(REPO / "mcp" / "servers.jsonc") as f:
        servers = json5_loads(f.read())
    with open(REPO / "mcp" / "servers.schema.json") as f:
        schema = json.load(f)
    jsonschema.validate(instance=servers, schema=schema)
    return servers


def load_config() -> dict:
    """Load and schema-check repo-wide settings (config.jsonc)."""
    with open(REPO / "config.jsonc") as f:
        config = json5_loads(f.read())
    with open(REPO / "config.schema.json") as f:
        schema = json.load(f)
    jsonschema.validate(instance=config, schema=schema)
    return config


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


def instructions_text(agent: str) -> str:
    """Global instructions for agent: agents/AGENTS.md (common guidelines)
    + <agent>/AGENTS.md (agent-specific additions, optional), combined.
    Keep both lean — they load into every session's context."""
    common = (AGENTS_SRC / "AGENTS.md").read_text().rstrip()
    extra = REPO / agent / "AGENTS.md"
    if extra.is_file():
        return f"{common}\n\n{extra.read_text().strip()}\n"
    return f"{common}\n"


def install_shared(servers: dict, config: dict, check_only: bool) -> bool:
    """Cross-agent 1:1 content (uniform INSTALLERS signature; servers and
    config unused). The ~/.agents dir itself is managed by other tooling
    (skills, .skill-lock.json) — only AGENTS.md is ours."""
    link(HOME / ".agents" / "AGENTS.md", AGENTS_SRC / "AGENTS.md", check_only)
    return True


# --- opencode ----------------------------------------------------------------


OPENCODE_DEST = HOME / ".config/opencode/opencode.jsonc"
OPENCODE_INSTRUCTIONS = HOME / ".config/opencode/AGENTS.md"


def install_opencode(servers: dict, config: dict, check_only: bool) -> bool:
    """Render opencode/opencode.jsonc.j2 → ~/.config/opencode/opencode.jsonc
    and generate AGENTS.md (common + opencode-specific guidelines)."""
    write_if_changed(
        OPENCODE_INSTRUCTIONS, instructions_text("opencode"), check_only
    )
    clean = {}
    for name, server in servers.items():
        rendered = for_agent(server, "opencode", keep_disabled=True)
        if rendered is not None:
            clean[name] = rendered
    rendered = JINJA.get_template("opencode/opencode.jsonc.j2").render(
        mcp_section=json.dumps({"servers": clean}, indent=2),
        home=str(HOME),
        enmaas_enabled=config["enmaas"]["enabled"],
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


def load_claude_settings(config: dict) -> dict:
    """Render claude/settings.jsonc.j2 (comments + {{ home }}/{{ repo }}
    substitution) and parse it (json5)."""
    rendered = JINJA.get_template("claude/settings.jsonc.j2").render(
        home=str(HOME),
        repo=str(REPO),
        enmaas_enabled=config["enmaas"]["enabled"],
        rely_on_glm=config["enmaas"].get("rely_on_glm", False),
    )
    return json5_loads(rendered)


def claude_symlink_map() -> dict[Path, Path]:
    """dest in ~/.claude -> source file/dir in the repo.

    Top-level files and files under agents/commands/hooks are linked
    individually; skills are linked as whole directories (mirrors the
    ~/.agents skills pattern). CLAUDE.md and settings.json are handled
    separately (both generated, see install_claude).
    """
    links: dict[Path, Path] = {}
    for name in ("statusline-command.sh",):
        links[CLAUDE_HOME / name] = CLAUDE_SRC / name
    for sub in ("agents", "commands", "hooks"):
        for src in sorted((CLAUDE_SRC / sub).iterdir()):
            if src.is_file():
                links[CLAUDE_HOME / sub / src.name] = src
    for src in sorted((CLAUDE_SRC / "skills").iterdir()):
        if src.is_dir():
            links[CLAUDE_HOME / "skills" / src.name] = src
    return links


def install_claude(servers: dict, config: dict, check_only: bool) -> bool:
    """Install claude/ files into ~/.claude, then plugins (incl. the mcp
    plugin). Returns False on validation error."""
    try:
        settings = load_claude_settings(config)
    except ValueError as e:
        print(f"INVALID: claude/settings.jsonc.j2 — {e}", file=sys.stderr)
        return False
    print("valid: claude/settings.jsonc.j2")
    # CLAUDE.md: common + claude-specific guidelines, generated (never
    # symlinked — a hand edit must surface as drift, not hit the repo).
    write_if_changed(
        CLAUDE_HOME / "CLAUDE.md",
        instructions_text("claude"),
        check_only,
        root=CLAUDE_HOME,
    )
    for dest, source in claude_symlink_map().items():
        link(dest, source, check_only, root=CLAUDE_HOME)

    # settings.json: rendered from settings.jsonc.j2 and copied, never
    # symlinked — Claude Code writes to it (permissions, /config).

    def drift_warn(old: dict, backup_path: Path | None) -> None:
        warn_drift(
            "~/.claude/settings.json", "claude/settings.jsonc.j2", old, settings, backup_path
        )

    write_if_changed(
        CLAUDE_HOME / "settings.json",
        json.dumps(settings, indent=2) + "\n",
        check_only,
        root=CLAUDE_HOME,
        parse=json.loads,
        warn=drift_warn,
    )
    return install_claude_plugins(servers, config, check_only)


# --- claude plugins: driven by claude/settings.jsonc.j2 ----------------------


def load_enabled_plugins(config: dict) -> list[str]:
    """Plugins to install: enabledPlugins=true in the settings template —
    the same list Claude Code uses to enable/disable."""
    enabled = load_claude_settings(config).get("enabledPlugins", {})
    if not isinstance(enabled, dict) or not all(
        isinstance(v, bool) for v in enabled.values()
    ):
        raise ValueError('"enabledPlugins" must map plugin ids to booleans')
    return [plugin for plugin, on in enabled.items() if on]


def load_marketplaces(config: dict) -> list[str]:
    """Marketplaces declared in the settings template (extraKnownMarketplaces)
    — the same place the claude CLI declares them. Returns add-able sources:
    github repos or local directory paths."""
    marketplaces = load_claude_settings(config).get("extraKnownMarketplaces", {})
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


def check_plugin_drift(config: dict) -> None:
    """Warn about user-scope plugins installed on this machine but not
    managed by the repo (e.g. installed manually via the CLI) — they would
    be lost on a fresh machine until added to the settings template."""
    installed_path = CLAUDE_HOME / "plugins" / "installed_plugins.json"
    try:
        installed = json.loads(installed_path.read_text()).get("plugins", {})
    except (OSError, ValueError):
        return  # nothing installed yet (fresh machine)
    managed = set(load_claude_settings(config).get("enabledPlugins", {}))
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


def apply_plugins(config: dict, check_only: bool) -> bool:
    """Add marketplaces + install plugins, both driven by the settings
    template (extraKnownMarketplaces / enabledPlugins). The claude CLI
    commands are idempotent no-ops when already present."""
    try:
        plugins = load_enabled_plugins(config)
        marketplaces = load_marketplaces(config)
    except ValueError as e:
        print(f"INVALID: claude/settings.jsonc.j2 — {e}", file=sys.stderr)
        return False
    print(f"valid: claude/settings.jsonc.j2 ({len(plugins)} plugins, "
          f"{len(marketplaces)} marketplaces)")
    if check_only:
        check_plugin_drift(config)
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
    check_plugin_drift(config)
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


def install_claude_plugins(servers: dict, config: dict, check_only: bool) -> bool:
    """The mcp plugin render + plugin application (--plugins shortcut)."""
    ok = render_mcp_plugin(servers, check_only)
    ok &= apply_plugins(config, check_only)
    return ok


# --- codex -------------------------------------------------------------------


CODEX_HOME = HOME / ".codex"

# Model profiles rendered as ~/.codex/<name>.config.toml, activated with
# `codex --profile <name>` (profiles are separate files layered over
# config.toml since codex 0.134): name -> (model, reasoning effort). config.toml
# sets GLM's high effort globally, so each profile pins its model's own codex
# default (as of codex 0.162) instead of inheriting it.
CODEX_PROFILES = {
    "gpt-luna": ("gpt-6-luna", "medium"),
    "gpt-sol": ("gpt-6-sol", "medium"),
    "gpt-astra": ("gpt-6-astra", "low"),
}

# {env:VAR} as a whole value (env entries in mcp/servers.jsonc)
ENV_VALUE_RE = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")

# The /model picker lists codex's model catalog, which knows nothing about
# GLM — and model_catalog_json *replaces* the catalog rather than adding to
# it. So with enmaas enabled we generate a catalog of exactly the EnMaaS
# models, derived from the catalog bundled in the codex binary (entries
# carry ~18K-char system prompts — too bulky and version-bound to commit).
CODEX_CATALOG = CODEX_HOME / "model-catalog.json"
CODEX_GLM_MODEL = "rits/zai-org/glm-5-3"
# The GLM entry is derived from this bundled entry, but only keeps the fields
# codex requires (found by elimination, codex 0.162) plus its system prompt.
# Every GPT-specific capability flag is left out so codex falls back to plain
# defaults — copying them breaks GLM's vLLM backend (supports_search_tool →
# "Unsupported input item type: additional_tools", apply_patch_tool_type
# freeform → "Custom tools with a grammar format are not supported").
CODEX_GLM_TEMPLATE = "gpt-6-luna"
CODEX_GLM_KEYS = (
    "shell_type",
    "visibility",
    "supported_in_api",
    "support_verbosity",
    "truncation_policy",
    "experimental_supported_tools",
    "base_instructions",
)


def codex_model_catalog() -> str | None:
    """JSON catalog of the EnMaaS models for codex's /model picker, or None
    (with a warning) when it can't be generated — codex then keeps its
    default catalog, minus GLM in the picker."""
    if shutil.which("codex") is None:
        print("WARNING: codex CLI not found — skipping model catalog (GLM won't be in /model)")
        return None
    result = subprocess.run(
        ["codex", "--no-daemon", "debug", "models", "--bundled"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        bundled = json.loads(result.stdout)
        by_slug = {m["slug"]: m for m in bundled["models"]}
        template = by_slug[CODEX_GLM_TEMPLATE]
        entries = [by_slug[model] for model, _ in CODEX_PROFILES.values()]
        glm = {k: template[k] for k in CODEX_GLM_KEYS} | {
            "slug": CODEX_GLM_MODEL,
            "display_name": "GLM 5.3",
            "description": "Hosted via EnMaaS",
            "priority": 0,
            # GLM runs at high effort (same as claude's modelSettings) — the
            # only level offered, and the default
            "default_reasoning_level": "high",
            "supported_reasoning_levels": [
                {
                    "effort": "high",
                    "description": "Greater reasoning depth for complex problems",
                }
            ],
        }
    except (ValueError, KeyError, TypeError) as e:
        print(
            f"WARNING: cannot derive codex model catalog from the bundled one "
            f"({e!r}) — skipping (GLM won't be in /model)"
        )
        return None
    # EnMaaS rejects service_tier=priority ("Fast") — advertise no tiers.
    entries = [glm] + [
        {k: v for k, v in e.items() if k not in ("service_tiers", "additional_speed_tiers")}
        for e in entries
    ]
    return json.dumps({"models": entries}, indent=2) + "\n"


def codex_mcp_toml(servers: dict) -> str:
    """mcp/servers.jsonc → codex [mcp_servers.*] tables.

    codex specifics (per codex docs, 2026-10): env values are literal (no
    expansion) — {env:VAR} placeholders become env_vars entries forwarded
    from codex's environment, renamed vars get a bash -c wrapper; http auth
    is bearer_token_env_var (a "codex" override), codex cannot read
    {file:...} header secrets; stdio cwd is native.
    """
    blocks = []
    for name, server in servers.items():
        merged = for_agent(server, "codex")
        if merged is None:
            continue
        lines = [f"[mcp_servers.{json.dumps(name)}]"]
        if merged["type"] == "remote":
            lines.append(f"url = {json.dumps(merged['url'])}")
            token_var = merged.get("bearer_token_env_var")
            if token_var:
                # replaces header auth — same token, from the environment
                lines.append(f"bearer_token_env_var = {json.dumps(token_var)}")
            elif merged.get("headers"):
                raise ValueError(
                    f"{name}: codex cannot read header secrets — add a "
                    '"codex": {"bearer_token_env_var": "..."} override'
                )
        else:
            command = list(merged["command"])
            env_vars, renames, literals = [], {}, {}
            for key, value in merged.get("environment", {}).items():
                m = ENV_VALUE_RE.fullmatch(value)
                if m:
                    env_vars.append(m.group(1))
                    if key != m.group(1):
                        renames[key] = m.group(1)
                else:
                    literals[key] = value
            if renames:
                # env_vars are forwarded verbatim — rename via bash wrapper
                exports = " ".join(
                    f'{key}="${{{var}}}"' for key, var in renames.items()
                )
                joined = " ".join(shlex.quote(c) for c in command)
                command = ["bash", "-c", f"export {exports} && exec {joined}"]
            lines.append(f"command = {json.dumps(command[0])}")
            lines.append(f"args = {json.dumps(command[1:])}")
            if merged.get("cwd"):
                lines.append(f"cwd = {json.dumps(merged['cwd'])}")
            if literals:
                lines.append(f"env = {json.dumps(literals)}")
            if env_vars:
                lines.append(f"env_vars = {json.dumps(env_vars)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def install_codex(servers: dict, config: dict, check_only: bool) -> bool:
    """Render codex/config.toml.j2 → ~/.codex/config.toml (+ model profile
    files), generate AGENTS.md (common + codex-specific guidelines). codex
    itself may write to config.toml (`codex mcp add`) — the write is
    drift-aware, like claude settings."""
    enmaas = config["enmaas"]["enabled"]
    write_if_changed(
        CODEX_HOME / "AGENTS.md", instructions_text("codex"), check_only
    )
    try:
        mcp_toml = codex_mcp_toml(servers)
    except ValueError as e:
        print(f"INVALID: mcp/servers.jsonc (codex rendering) — {e}", file=sys.stderr)
        return False
    catalog = codex_model_catalog() if enmaas else None
    changed = False
    if catalog is not None:
        # generated from the bundled catalog — derivable, no backup needed
        changed |= write_if_changed(
            CODEX_CATALOG, catalog, check_only, parse=json.loads, keep_backup=False
        )
    rendered = JINJA.get_template("codex/config.toml.j2").render(
        enmaas_enabled=enmaas,
        mcp_toml=mcp_toml,
        home=str(HOME),
        catalog_path=str(CODEX_CATALOG) if catalog is not None else None,
    )
    try:
        new = tomllib.loads(rendered)
    except ValueError as e:
        print(f"INVALID: codex/config.toml.j2 — {e}", file=sys.stderr)
        return False
    print("valid: codex/config.toml.j2")

    def drift_warn(old: dict, backup_path: Path | None) -> None:
        warn_drift("~/.codex/config.toml", "codex/config.toml.j2", old, new, backup_path)

    changed |= write_if_changed(
        CODEX_HOME / "config.toml",
        rendered,
        check_only,
        root=CODEX_HOME,
        parse=tomllib.loads,
        warn=drift_warn,
    )
    if enmaas:  # profiles point at the enmaas provider — none when disabled
        for name, (model, effort) in CODEX_PROFILES.items():
            text = JINJA.get_template("codex/profile.config.toml.j2").render(
                name=name, model=model, effort=effort
            )
            changed |= write_if_changed(
                CODEX_HOME / f"{name}.config.toml", text, check_only
            )
    if changed and not check_only:
        # the managed app-server daemon loads config once, at startup —
        # running it from here would break its socket (see docs), so just say so
        print(
            "NOTE: codex config changed — a running codex app-server daemon "
            "keeps the old config; run `codex app-server daemon restart` "
            "(interrupts running sessions) or restart codex to apply"
        )
    return True


# --- omp (oh-my-pi) -----------------------------------------------------------
#
# omp is a fork of pi with its own binary and config dir (~/.omp/agent).
# Secrets use the command form (!cat/!printenv — omp runs values starting
# with "!" as shell commands at request/connect time): no env-var magic
# names, no inlined secret contents.


OMP_HOME = HOME / ".omp" / "agent"
OMP_GLM_PROVIDER = "enmaas-glm"
OMP_GLM_MODEL = "rits/zai-org/glm-5-3"
# enmaas-off fallback: direct OpenAI via the key file (same secret and
# default model as opencode's fallback)
OMP_FALLBACK_PROVIDER = "openai-key"
OMP_FALLBACK_MODEL = "gpt-6-luna"

# {env:VAR} / {file:PATH} placeholders (mcp/servers.jsonc opencode syntax)
OMP_PLACEHOLDER_RE = re.compile(r"\{(env|file):([^}]+)\}")


def omp_secret_value(value: str) -> str:
    """opencode placeholder -> omp command value.

    A placeholder as the whole value becomes a plain !cat/!printenv; one
    embedded in a larger value (e.g. "Bearer {file:...}") becomes an echo
    with command substitution — double-quoted so the substitution runs and
    literal parts keep their spaces (breaks if the value itself contains
    double quotes or dollar signs; ours don't).
    """
    whole = OMP_PLACEHOLDER_RE.fullmatch(value)
    if whole:
        kind, ref = whole.groups()
        if kind == "file":
            return "!cat " + shlex.quote(str(Path(ref).expanduser()))
        return f"!printenv {ref}"
    if not OMP_PLACEHOLDER_RE.search(value):
        return value

    def sub(m: re.Match) -> str:
        kind, ref = m.group(1), m.group(2)
        if kind == "file":
            return "$(cat " + shlex.quote(str(Path(ref).expanduser())) + ")"
        return f"$(printenv {ref})"

    return '!echo "' + OMP_PLACEHOLDER_RE.sub(sub, value) + '"'


def omp_mcp_json(servers: dict) -> str:
    """mcp/servers.jsonc → omp mcp.json (same shape as pi's).

    Notes: per-server opencode "timeout" has no documented omp equivalent
    and is dropped; secrets translate to !commands (omp_secret_value).
    """
    entries = {}
    for name, server in servers.items():
        merged = for_agent(server, "omp")
        if merged is None:
            continue
        if merged["type"] == "remote":
            out = {"type": "http", "url": merged["url"]}
            if "oauth" in merged:
                out["oauth"] = merged["oauth"]
            headers = {
                k: omp_secret_value(v) for k, v in merged.get("headers", {}).items()
            }
            if headers:
                out["headers"] = headers
        else:
            command = merged["command"]
            out = {"type": "stdio", "command": command[0], "args": command[1:]}
            if merged.get("cwd"):
                out["cwd"] = merged["cwd"]
            if merged.get("environment"):
                out["env"] = {
                    k: omp_secret_value(v)
                    for k, v in merged["environment"].items()
                }
        entries[name] = out
    return json.dumps({"mcpServers": entries}, indent=2) + "\n"


def set_omp_default_model(config: dict, check_only: bool) -> None:
    """Route omp's default model — EnMaaS GLM when enabled (config.jsonc),
    direct OpenAI via the key file otherwise (same fallback as opencode) —
    surgically.

    config.yml is omp-owned (/settings, `omp config set` write to it), so
    unlike the other generated configs we never render it wholesale: only
    modelRoles.default changes, omp's own settings survive. The yaml
    round-trip drops comments, which omp rewrites anyway.
    """
    path = OMP_HOME / "config.yml"
    if path.is_symlink():
        print(f"WARNING: {path} is a symlink — not writing through it")
        return
    data: dict = {}
    if path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            print(f"WARNING: {path} is not valid YAML — leaving it alone")
            return
        data = loaded if isinstance(loaded, dict) else {}
    roles = data.get("modelRoles")
    roles = roles if isinstance(roles, dict) else {}
    current = roles.get("default")
    if config["enmaas"]["enabled"]:
        wanted = f"{OMP_GLM_PROVIDER}/{OMP_GLM_MODEL}"
    else:
        wanted = f"{OMP_FALLBACK_PROVIDER}/{OMP_FALLBACK_MODEL}"
    if current == wanted:
        print(f"unchanged: {path} (modelRoles.default)")
        return
    if check_only:
        print(f"would set modelRoles.default = {wanted} in {path}")
        return
    if path.is_file():
        backup(path)
    roles["default"] = wanted
    data["modelRoles"] = roles
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, sort_keys=False))
    print(f"installed: {path}")


def install_omp(servers: dict, config: dict, check_only: bool) -> bool:
    """Render omp/ templates → ~/.omp/agent: models.yml (EnMaaS providers,
    key read via !cat at request time), mcp.json (from mcp/servers.jsonc,
    placeholders → !commands), AGENTS.md (common guidelines — without it
    omp falls back to reading ~/.claude/CLAUDE.md), plus the surgical
    modelRoles.default edit in omp-owned config.yml."""
    write_if_changed(OMP_HOME / "AGENTS.md", instructions_text("omp"), check_only)
    models = JINJA.get_template("omp/models.yml.j2").render(home=str(HOME))
    try:
        yaml.safe_load(models)
    except yaml.YAMLError as e:
        print(f"INVALID: omp/models.yml.j2 — {e}", file=sys.stderr)
        return False
    print("valid: omp/models.yml.j2")
    write_if_changed(
        OMP_HOME / "models.yml", models, check_only, parse=yaml.safe_load
    )
    write_if_changed(
        OMP_HOME / "mcp.json", omp_mcp_json(servers), check_only, parse=json.loads
    )
    set_omp_default_model(config, check_only)
    return True


# --- main --------------------------------------------------------------------


INSTALLERS = (
    install_shared,
    install_opencode,
    install_claude,
    install_codex,
    install_omp,
)


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
    try:
        config = load_config()
    except (ValueError, jsonschema.ValidationError) as e:
        print(f"INVALID: config.jsonc — {e}", file=sys.stderr)
        return 1
    print("valid: config.jsonc")
    if args.plugins:
        return 0 if install_claude_plugins(servers, config, args.check) else 1
    ok = True
    for installer in INSTALLERS:
        try:
            ok &= installer(servers, config, args.check)
        except Exception as e:  # one agent failing must not abort the others
            ok = False
            print(f"FAILED: {installer.__name__} — {e!r}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
