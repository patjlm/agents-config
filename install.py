# /// script
# requires-python = ">=3.11"
# dependencies = ["jinja2", "json5", "jsonschema", "pyyaml"]
# ///
"""Render agent config templates, validate, and install them.

Usage:
    uv run install.py             # render + validate + install (all agents)
    uv run install.py --check     # render + validate only, write nothing
    uv run install.py --plan      # --check plus a unified diff of each change
    uv run install.py --plugins   # only claude plugins (incl. the mcp plugin)

Structure: shared primitives, shared data, then one section per agent —
INSTALLERS lists them, so adding an agent is a new section + one entry.
Agent-specific *formats* live in the agent's folder as Jinja templates
(opencode.jsonc.j2, settings.jsonc.j2); agent-specific *behavior* (symlinks,
CLI calls, drift checks) lives in the agent's section here.

Validation:
    - Rendered configs: syntax check (json5 parse). The opencode config is
      additionally validated against the vendored v2 schema
      (opencode/config.schema.json, published at https://opencode.ai/config.json
      — refresh it when upgrading opencode), and rendered opencode subagent
      templates are validated as v2 `agents` entries.
    - Shared data (mcp/servers.jsonc): checked against mcp/servers.schema.json
      (self-owned, so it tracks reality).
    - claude/settings.jsonc.j2: parse check (json5 — comments allowed) before
      rendering to ~/.claude/settings.json.

Install mechanisms (see AGENTS.md):
    - opencode: template render → ~/.config/opencode/opencode.jsonc.
    - claude: 1:1 files (statusline, commands/, hooks/) → symlinks into
      ~/.claude; skills and subagents come from the shared mechanisms below.
      settings.jsonc.j2 is rendered to
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
    - skills: shared skills/ (plus <agent>/skills/ for agent-only ones) →
      whole-directory symlinks into ~/.claude/skills, ~/.agents/skills
      (read by codex, pi and opencode) and ~/.omp/agent/skills. A SKILL.md
      frontmatter `targets: [claude, omp]` limits a skill to those agents;
      absent means every agent. Destinations follow skillshare's maintained
      registry (see AGENTS.md). Stale links into this repo are pruned, and
      backups go under <agent dir>/backups — a .bak next to the skills would
      be discovered as a skill.
    - subagents: shared subagents/*.md (linked 1:1) and *.md.j2 (rendered per
      agent with `agent` in the context) → ~/.claude/agents and
      ~/.config/opencode/agents, the only agents that read definition files.
      One template can carry both frontmatter dialects (claude's tools/model
      versus opencode's mode/permissions/model id). `targets:` in the
      frontmatter limits a definition; absent means both.
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
      to reading ~/.claude/CLAUDE.md), and surgical modelRoles/advisor
      edits in omp-owned config.yml (config.yml is written by omp itself,
      /settings and `omp config set`, so it is never rendered wholesale).
    - pi: pi/models.json.j2 → ~/.pi/agent/models.json (EnMaaS providers +
      litellm proxy, apiKey via !cat), pi/settings.json.j2 →
      ~/.pi/agent/settings.json (drift-aware — pi writes theme, packages,
      changelog version there), mcp.json (shared pi-family translation,
      needs pi 1.x), AGENTS.md generation, and 1:1 symlinks for
      extensions/ + settings/ (gathered from the pre-repo setup;
      runtime-written *.config.json files stay unmanaged).
    - harnesses: harnesses/<name>/harness.jsonc + AGENTS.md (task content) with
      <agent>/harness/ templates → ~/.pi/harnesses/<name>/: rendered mcp.json
      (selected servers + per-harness overrides) and settings.json (pinned
      model), symlinked AGENTS.md/models.json/skills, and an executable `pi`
      launcher that sets PI_CODING_AGENT_DIR, CA trust and the tool policy.
      That launcher is also the T3 Code pi provider binary (binary path).

Idempotent: skips destinations whose content already matches the render,
backs up differing destinations before overwriting, never writes through
symlinks. One agent failing does not abort the others.
"""
import argparse
import difflib
import functools
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
# --plan: check mode plus a unified diff of every file that would change,
# so an MCP server definition can be previewed in each agent's native format.
PLAN = False

JINJA = Environment(
    loader=FileSystemLoader(str(REPO)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)
# Markdown templates: lstrip/trim keep conditionals from leaving blank lines
# behind (a blank line inside frontmatter or a list is visible in the file).
JINJA_MD = Environment(
    loader=FileSystemLoader(str(REPO)),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
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


def plan_diff(dest: Path, text: str) -> None:
    """Unified diff of what is at dest now against what would be written.

    Only regular files: a symlink is replaced rather than compared, and a
    missing file has nothing to diff. Rendered content holds secret *references*
    (env vars, !cat commands), never values."""
    if dest.is_symlink() or not dest.is_file():
        return
    sys.stdout.writelines(
        difflib.unified_diff(
            dest.read_text().splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile=f"{dest} (current)",
            tofile=f"{dest} (rendered)",
        )
    )


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
        if PLAN:
            plan_diff(dest, text)
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
AGENTS = ("claude", "codex", "opencode", "omp", "pi")


@functools.cache
def mcp_server_schema() -> dict:
    """mcp/servers.schema.json — one `server` definition per key."""
    with open(REPO / "mcp" / "servers.schema.json") as f:
        return json.load(f)


def load_mcp_servers() -> dict:
    """Load and schema-check the canonical MCP server list."""
    with open(REPO / "mcp" / "servers.jsonc") as f:
        servers = json5_loads(f.read())
    schema = mcp_server_schema()
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
    config unused): ~/.agents/AGENTS.md and ~/.agents/skills. The skills
    directory is read by codex, pi and opencode (see the skills section);
    it used to belong to the `skills` CLI, install.py owns it now."""
    link(HOME / ".agents" / "AGENTS.md", AGENTS_SRC / "AGENTS.md", check_only)
    sync_skills(
        HOME / ".agents" / "skills",
        ("codex", "pi", "opencode"),
        check_only,
        root=HOME / ".agents",
    )
    return True


# --- shared: skills/ ---------------------------------------------------------
#
# One shared skill tree, fanned out as whole-directory symlinks: a skill is a
# directory (SKILL.md + resources), so linking the directory means a resource
# added in the repo needs no reinstall, and a skill is added/removed atomically.
# Destinations come from skillshare's maintained agent registry
# (https://github.com/runkids/skillshare/blob/main/internal/config/targets.yaml):
#   ~/.claude/skills       claude
#   ~/.agents/skills       codex, pi and opencode all scan it (also_scans)
#   ~/.omp/agent/skills    omp scans only its own directory
# A SKILL.md frontmatter `targets: [claude, omp]` limits a skill to those
# agents; no `targets` means every agent. Skills that are specific to one
# agent live under <agent>/skills/ and are linked by that agent's section.

SKILLS_SRC = REPO / "skills"
AGENT_NAMES = ("claude", "codex", "omp", "opencode", "pi")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
# Fallback for frontmatter yaml refuses (see parse_targets): inline form only.
TARGETS_RE = re.compile(r"^targets:\s*\[([^\]]*)\]", re.M)


@functools.cache
def parse_targets(text: str, label: str) -> tuple[str, ...] | None:
    """`targets:` from a definition's frontmatter; None = every agent."""
    match = FRONTMATTER_RE.match(text)
    front = match.group(1) if match else ""
    try:
        data = yaml.safe_load(front) or {}
    except yaml.YAMLError:
        # Hand-written agent definitions are not always strict YAML (an
        # unquoted colon in a description breaks yaml, though Claude's own
        # parser accepts it). Only `targets:` matters here, so read the
        # inline form directly and treat anything else as "every agent".
        inline = TARGETS_RE.search(front)
        data = {"targets": inline.group(1).split(",") if inline else None}
    targets = data.get("targets")
    if targets is None:
        return None
    if isinstance(targets, str):
        targets = [targets]
    if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
        raise ValueError(f'{label}: "targets" must be a list of agent names')
    targets = [t.strip().strip("'\"") for t in targets]
    unknown = sorted(set(targets).difference(AGENT_NAMES))
    if unknown:
        print(
            f"WARNING: {label}: unknown targets {unknown} "
            f"(known: {', '.join(AGENT_NAMES)})"
        )
    return tuple(targets)


@functools.cache
def skill_targets(skill_dir: Path) -> tuple[str, ...] | None:
    """`targets:` from a shared skill's SKILL.md; None = every agent."""
    return parse_targets((skill_dir / "SKILL.md").read_text(), skill_dir.name)


@functools.cache
def shared_skill_dirs() -> tuple[tuple[str, Path], ...]:
    """Every shared skill directory (name, path), warned about once."""
    found = []
    for src in sorted(SKILLS_SRC.iterdir()):
        if not src.is_dir():
            continue
        if not (src / "SKILL.md").is_file():
            print(f"WARNING: skills/{src.name}: no SKILL.md — skipped")
            continue
        found.append((src.name, src))
    return tuple(found)


def shared_skills(agents: tuple[str, ...]) -> dict[str, Path]:
    """Shared skills these agents may read (frontmatter targets, absent = all)."""
    wanted: dict[str, Path] = {}
    for name, src in shared_skill_dirs():
        targets = skill_targets(src)
        if targets is None or set(agents).intersection(targets):
            wanted[name] = src
    return wanted


def agent_skills(agent: str) -> dict[str, Path]:
    """<agent>/skills/* — skills for that agent only."""
    root = REPO / agent / "skills"
    if not root.is_dir():
        return {}
    return {src.name: src for src in sorted(root.iterdir()) if src.is_dir()}


def prune_repo_links(dest_dir: Path, wanted: set[str], check_only: bool) -> None:
    """Remove our stale skill links (symlinks resolving into this repo).
    Real directories and foreign links — app-installed skills, say — stay."""
    if not dest_dir.is_dir():
        return
    for entry in sorted(dest_dir.iterdir()):
        if entry.name in wanted or not entry.is_symlink():
            continue
        target = entry.resolve()
        if not target.is_relative_to(REPO):
            continue
        if check_only:
            print(f"would remove: {entry} -> {target}")
        else:
            entry.unlink()
            print(f"removed: {entry} -> {target}")


def sync_skills(
    dest_dir: Path,
    agents: tuple[str, ...],
    check_only: bool,
    root: Path,
    extra: dict[str, Path] | None = None,
) -> None:
    """Link shared skills (filtered by `targets:`) plus agent-only ones into
    dest_dir, then prune our stale links. Backups go under root/backups — a
    .bak directory next to the skills would be discovered as a skill."""
    wanted = shared_skills(agents)
    wanted.update(extra or {})
    for name, src in sorted(wanted.items()):
        link(dest_dir / name, src, check_only, root=root)
    prune_repo_links(dest_dir, set(wanted), check_only)


# --- shared: subagents/ ------------------------------------------------------
#
# Agent definitions for the agents that read them: claude (~/.claude/agents)
# and opencode (~/.config/opencode/agents). A plain .md is linked 1:1; a
# .md.j2 is rendered per agent with `agent` in the context, so one source can
# carry both frontmatter dialects — claude's `tools:`/`model: <alias or id>`
# versus opencode's `mode:`/`permissions:`/`model: provider/id`. `targets:` in
# the (rendered) frontmatter limits a definition to some agents; absent means
# every agent that reads definitions. Generated definitions are not pruned —
# like other rendered configs, removing the template leaves the last render.

SUBAGENTS_SRC = REPO / "subagents"


def sync_subagents(
    agent: str,
    dest_dir: Path,
    config: dict,
    check_only: bool,
    root: Path,
) -> None:
    """Place subagent definitions for one agent, then prune our stale links.
    Backups go under root/backups, never next to the definitions."""
    wanted: dict[str, tuple[Path, str | None]] = {}
    for src in sorted(SUBAGENTS_SRC.iterdir()):
        templated = src.name.endswith(".md.j2")
        if not templated and src.suffix != ".md":
            continue
        name = src.name[: -len(".j2")] if templated else src.name
        text = (
            JINJA_MD.get_template(f"subagents/{src.name}").render(
                agent=agent,
                enmaas_enabled=config["enmaas"]["enabled"],
                home=str(HOME),
                repo=str(REPO),
            )
            if templated
            else src.read_text()
        )
        targets = parse_targets(text, name)
        if targets is not None and agent not in targets:
            continue
        if templated and agent == "opencode":
            # Frontmatter must be a valid v2 agents entry (defined below).
            validate_opencode_agent(name, text)
        wanted[name] = (src, text if templated else None)
    for name, (src, text) in sorted(wanted.items()):
        if text is None:
            link(dest_dir / name, src, check_only, root=root)
        else:
            write_if_changed(dest_dir / name, text, check_only, root=root)
    prune_repo_links(dest_dir, set(wanted), check_only)


# --- opencode ----------------------------------------------------------------
#
# Skills: none linked here — opencode scans both ~/.claude/skills and
# ~/.agents/skills, which the claude and shared sections populate (see the
# shared skills section).


OPENCODE_DEST = HOME / ".config/opencode/opencode.jsonc"
OPENCODE_AGENTS = HOME / ".config/opencode/agents"
OPENCODE_INSTRUCTIONS = HOME / ".config/opencode/AGENTS.md"
# Published OpenCode v2 schema, vendored so installs validate offline. Refresh
# it when upgrading OpenCode:
#   curl -fsSL https://opencode.ai/config.json -o opencode/config.schema.json
OPENCODE_SCHEMA = REPO / "opencode" / "config.schema.json"


@functools.cache
def opencode_schema() -> dict:
    return json.loads(OPENCODE_SCHEMA.read_text())


def validate_opencode_agent(name: str, text: str) -> None:
    """Check a rendered definition's frontmatter as a v2 `agents` entry
    (mode, model, permissions actions/effects)."""
    match = FRONTMATTER_RE.match(text)
    try:
        front = yaml.safe_load(match.group(1)) if match else {}
    except yaml.YAMLError as e:
        raise ValueError(f"{name}: invalid frontmatter — {e}") from e
    try:
        jsonschema.validate({"agents": {name: front}}, opencode_schema())
    except jsonschema.ValidationError as e:
        raise ValueError(f"{name}: not a valid OpenCode v2 agent — {e.message}") from e


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
        rendered_config = json5_loads(rendered)
    except ValueError as e:
        print(f"INVALID: {OPENCODE_DEST} — {e}", file=sys.stderr)
        return False
    try:
        jsonschema.validate(rendered_config, opencode_schema())
    except jsonschema.ValidationError as e:
        where = "/".join(map(str, e.absolute_path)) or "(root)"
        print(
            f"INVALID: {OPENCODE_DEST} — {e.message} (at {where})",
            file=sys.stderr,
        )
        return False
    print(f"valid: {OPENCODE_DEST}")
    write_if_changed(OPENCODE_DEST, rendered, check_only)
    sync_subagents(
        "opencode",
        OPENCODE_AGENTS,
        config,
        check_only,
        root=HOME / ".config/opencode",
    )
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
        aux_model=config["enmaas"].get("aux_model"),
    )
    return json5_loads(rendered)


def claude_symlink_map() -> dict[Path, Path]:
    """dest in ~/.claude -> source file/dir in the repo.

    Top-level files and files under commands/hooks are linked individually.
    Skills are linked by sync_skills (shared skills/ filtered by `targets:`,
    plus claude/skills/ for Claude-only ones); subagents by sync_subagents.
    CLAUDE.md and settings.json are handled separately (both generated, see
    install_claude).
    """
    links: dict[Path, Path] = {}
    for name in ("statusline-command.sh",):
        links[CLAUDE_HOME / name] = CLAUDE_SRC / name
    for sub in ("commands", "hooks"):
        for src in sorted((CLAUDE_SRC / sub).iterdir()):
            if src.is_file():
                links[CLAUDE_HOME / sub / src.name] = src
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
    sync_skills(
        CLAUDE_HOME / "skills",
        ("claude",),
        check_only,
        root=CLAUDE_HOME,
        extra=agent_skills("claude"),
    )
    sync_subagents("claude", CLAUDE_HOME / "agents", config, check_only, root=CLAUDE_HOME)

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
    manages (e.g. a pre-existing hand-added entry) would show up as
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
#
# Skills: none linked here — codex reads ~/.agents/skills, which
# install_shared populates (see the shared skills section).


CODEX_HOME = HOME / ".codex"

# Model profiles rendered as ~/.codex/<name>.config.toml, activated with
# `codex --profile <name>` (profiles are separate files layered over
# config.toml since codex 0.134): name -> (model, reasoning effort). config.toml
# sets GLM's high effort globally, so each profile pins its model's own codex
# default (as of codex 0.162) instead of inheriting it. The deepseek profile
# is the secondary EnMaaS option next to the GLM default (medium effort —
# Flash-class model; low/high are accepted too, see CODEX_SYNTH_MODELS).
CODEX_PROFILES = {
    "gpt-luna": ("gpt-6-luna", "medium"),
    "gpt-sol": ("gpt-6-sol", "medium"),
    "gpt-astra": ("gpt-6-astra", "low"),
    "deepseek": ("rits/deepseek-ai/DeepSeek-V4.1-Flash-1017", "medium"),
}

# {env:VAR} as a whole value (env entries in mcp/servers.jsonc)
ENV_VALUE_RE = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")

# The /model picker lists codex's model catalog, which knows nothing about
# GLM — and model_catalog_json *replaces* the catalog rather than adding to
# it. So with enmaas enabled we generate a catalog of exactly the EnMaaS
# models, derived from the catalog bundled in the codex binary (entries
# carry ~18K-char system prompts — too bulky and version-bound to commit).
CODEX_CATALOG = CODEX_HOME / "model-catalog.json"
# EnMaaS models absent from codex's bundled catalog: slug -> catalog spec.
# GLM offers high effort only; DeepSeek Flash accepts low/medium/high
# (verified against /v1/responses) and defaults to medium.
CODEX_SYNTH_MODELS = {
    "rits/zai-org/glm-5-3": {
        "display_name": "GLM 5.3",
        "default_reasoning_level": "high",
        "supported_reasoning_levels": [
            {
                "effort": "high",
                "description": "Greater reasoning depth for complex problems",
            }
        ],
    },
    "rits/deepseek-ai/DeepSeek-V4.1-Flash-1017": {
        "display_name": "DeepSeek V4.1 Flash",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": [
            {"effort": "low", "description": "Light reasoning for quick answers"},
            {"effort": "medium", "description": "Balanced reasoning depth"},
            {"effort": "high", "description": "Greater reasoning depth for complex problems"},
        ],
    },
}
# Synthesized entries are derived from this bundled entry, but keep only the
# fields codex requires (found by elimination, codex 0.162) plus its system
# prompt. Every GPT-specific capability flag is left out so codex falls back
# to plain defaults — copying them breaks vLLM backends like GLM's and
# DeepSeek's (supports_search_tool → "Unsupported input item type:
# additional_tools", apply_patch_tool_type freeform → "Custom tools with a
# grammar format are not supported").
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
    default catalog, minus the EnMaaS models in the picker."""
    if shutil.which("codex") is None:
        print(
            "WARNING: codex CLI not found — skipping model catalog "
            "(EnMaaS models won't be in /model)"
        )
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
        # profile models present in the bundled catalog (the EnMaaS-only
        # ones — GLM, DeepSeek — are synthesized from the template instead)
        entries = [
            by_slug[model]
            for model, _ in CODEX_PROFILES.values()
            if model in by_slug
        ]
        synth = [
            {k: template[k] for k in CODEX_GLM_KEYS}
            | {"slug": slug, "description": "Hosted via EnMaaS", "priority": 0}
            | spec
            for slug, spec in CODEX_SYNTH_MODELS.items()
        ]
    except (ValueError, KeyError, TypeError) as e:
        print(
            f"WARNING: cannot derive codex model catalog from the bundled one "
            f"({e!r}) — skipping (EnMaaS models won't be in /model)"
        )
        return None
    # EnMaaS rejects service_tier=priority ("Fast") — advertise no tiers.
    entries = synth + [
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


# --- pi family: shared mcp translation (pi, omp) ------------------------------
#
# pi and omp (its fork) share the mcp.json shape, and both resolve values
# starting with "!" as shell commands at connect time — secrets never
# inline, no env-var magic names.


# {env:VAR} / {file:PATH} placeholders (mcp/servers.jsonc opencode syntax)
PI_PLACEHOLDER_RE = re.compile(r"\{(env|file):([^}]+)\}")


def pi_secret_value(value: str) -> str:
    """opencode placeholder -> pi-family command value.

    A placeholder as the whole value becomes a plain !cat/!printenv; one
    embedded in a larger value (e.g. "Bearer {file:...}") becomes an echo
    with command substitution — double-quoted so the substitution runs and
    literal parts keep their spaces (breaks if the value itself contains
    double quotes or dollar signs; ours don't).
    """
    whole = PI_PLACEHOLDER_RE.fullmatch(value)
    if whole:
        kind, ref = whole.groups()
        if kind == "file":
            return "!cat " + shlex.quote(str(Path(ref).expanduser()))
        return f"!printenv {ref}"
    if not PI_PLACEHOLDER_RE.search(value):
        return value

    def sub(m: re.Match) -> str:
        kind, ref = m.group(1), m.group(2)
        if kind == "file":
            return "$(cat " + shlex.quote(str(Path(ref).expanduser())) + ")"
        return f"$(printenv {ref})"

    return '!echo "' + PI_PLACEHOLDER_RE.sub(sub, value) + '"'


def pi_mcp_json(servers: dict, agent: str) -> str:
    """mcp/servers.jsonc → pi-family mcp.json, the agent's override block
    applied (see for_agent).

    Notes: per-server opencode "timeout" has no documented pi/omp
    equivalent and is dropped; secrets translate to !commands
    (pi_secret_value). "oauth": false (opencode's opt-out of OAuth
    auto-detection) is dropped for pi: its loader only accepts an oauth
    object, so a boolean is inexpressible there; omp accepts both.
    """
    entries = {}
    for name, server in servers.items():
        merged = for_agent(server, agent)
        if merged is None:
            continue
        if merged["type"] == "remote":
            out = {"type": "http", "url": merged["url"]}
            oauth = merged.get("oauth")
            if oauth is not None and not (agent == "pi" and isinstance(oauth, bool)):
                out["oauth"] = oauth
            headers = {
                k: pi_secret_value(v) for k, v in merged.get("headers", {}).items()
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
                    k: pi_secret_value(v)
                    for k, v in merged["environment"].items()
                }
        entries[name] = out
    return json.dumps({"mcpServers": entries}, indent=2) + "\n"


# --- omp (oh-my-pi) -----------------------------------------------------------
#
# omp is a fork of pi with its own binary and config dir (~/.omp/agent).
# Secrets use the command form (!cat/!printenv — omp runs values starting
# with "!" as shell commands at request/connect time): no env-var magic
# names, no inlined secret contents.


OMP_HOME = HOME / ".omp" / "agent"
OMP_GLM_PROVIDER = "enmaas-glm"
OMP_GLM_MODEL = "rits/zai-org/glm-5-3"
# main model: DeepSeek (vision-capable) with GLM as the passively
# reviewing advisor (omp's advisor runtime) — both free on EnMaaS
OMP_DEEPSEEK_PROVIDER = "enmaas-deepseek"
OMP_DEEPSEEK_MODEL = "rits/deepseek-ai/DeepSeek-V4.1-Flash-1017"
# enmaas-off fallback: direct OpenAI via the key file (same secret and
# default model as opencode's fallback) — advisor dropped, the single
# fallback model would only review itself
OMP_FALLBACK_PROVIDER = "openai-key"
OMP_FALLBACK_MODEL = "gpt-6-luna"


def set_omp_model_roles(config: dict, check_only: bool) -> None:
    """Route omp's model roles — EnMaaS DeepSeek as the main model with
    GLM as advisor (advisor runtime enabled) when enmaas is on
    (config.jsonc), direct OpenAI via the key file otherwise (same
    fallback as opencode) — surgically.

    config.yml is omp-owned (/settings, `omp config set` write to it), so
    unlike the other generated configs we never render it wholesale: only
    modelRoles.default/advisor and advisor.enabled change, omp's own
    settings survive. The yaml round-trip drops comments, which omp
    rewrites anyway.
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
    advisor = data.get("advisor")
    advisor = advisor if isinstance(advisor, dict) else {}
    if config["enmaas"]["enabled"]:
        wanted_roles = {
            "default": f"{OMP_DEEPSEEK_PROVIDER}/{OMP_DEEPSEEK_MODEL}",
            "advisor": f"{OMP_GLM_PROVIDER}/{OMP_GLM_MODEL}",
        }
        wanted_advisor_enabled = True
    else:
        wanted_roles = {
            "default": f"{OMP_FALLBACK_PROVIDER}/{OMP_FALLBACK_MODEL}",
        }
        wanted_advisor_enabled = False
    # unchanged iff every wanted role matches, no managed role lingers
    # that shouldn't (advisor when enmaas is off), and advisor.enabled
    # matches
    stale = ({"default", "advisor"} - wanted_roles.keys()) & roles.keys()
    if (
        not stale
        and all(roles.get(k) == v for k, v in wanted_roles.items())
        and advisor.get("enabled") == wanted_advisor_enabled
    ):
        print(f"unchanged: {path} (modelRoles/advisor)")
        return
    if check_only:
        print(
            f"would set modelRoles = {wanted_roles}, "
            f"advisor.enabled = {wanted_advisor_enabled} in {path}"
        )
        return
    if path.is_file():
        backup(path)
    for key, value in wanted_roles.items():
        roles[key] = value
    if "advisor" not in wanted_roles:
        roles.pop("advisor", None)
    data["modelRoles"] = roles
    advisor["enabled"] = wanted_advisor_enabled
    data["advisor"] = advisor
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, sort_keys=False))
    print(f"installed: {path}")


def install_omp(servers: dict, config: dict, check_only: bool) -> bool:
    """Render omp/ templates → ~/.omp/agent: models.yml (EnMaaS providers,
    key read via !cat at request time), mcp.json (from mcp/servers.jsonc,
    placeholders → !commands), AGENTS.md (common guidelines — without it
    omp falls back to reading ~/.claude/CLAUDE.md), plus the surgical
    modelRoles/advisor edits in omp-owned config.yml."""
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
        OMP_HOME / "mcp.json", pi_mcp_json(servers, "omp"), check_only, parse=json.loads
    )
    set_omp_model_roles(config, check_only)
    sync_skills(OMP_HOME / "skills", ("omp",), check_only, root=OMP_HOME)
    return True


# --- pi ------------------------------------------------------------------------
#
# Skills: none linked here — pi reads ~/.agents/skills, which install_shared
# populates (see the shared skills section).


PI_HOME = HOME / ".pi" / "agent"
PI_GLM_PROVIDER = "enmaas-glm"
PI_GLM_MODEL = "rits/zai-org/glm-5-3"
# enmaas-off fallback: the vertex provider (pi-vertex extension package,
# gcloud ADC) — same fallback family as claude, so pi needs no openai-key
# provider (unlike omp). opus-4-6, not the pre-repo haiku-4-5: vertex
# rejects adaptive thinking on haiku-4-5 and modelOverrides cannot reach
# extension-registered models to disable it.
PI_FALLBACK_PROVIDER = "vertex"
PI_FALLBACK_MODEL = "claude-opus-4-6"

# @lhl/pi-vertex 1.1.9 (latest, May 2026) predates pi 1.x: it registers
# its models with an empty baseUrl, which pi's registration validator
# rejects (github.com/lhl/pi-vertex issue #2, fix proposed but unreleased).
# Patch the installed copy until a release ships it.
PI_VERTEX_ENTRY = PI_HOME / "npm" / "node_modules" / "@lhl" / "pi-vertex" / "index.ts"
PI_VERTEX_BROKEN = 'baseUrl: "", // Will be set dynamically'
PI_VERTEX_FIXED = (
    'baseUrl: "https://aiplatform.googleapis.com", '
    "// Placeholder; actual per-region URL is built in streamVertex()"
)


def patch_pi_vertex(check_only: bool) -> None:
    """Apply the pi-vertex issue #2 fix to the installed package copy
    (idempotent; skipped when the package is not installed — pi installs
    it from settings.json packages on first run)."""
    path = PI_VERTEX_ENTRY
    if not path.is_file():
        return
    text = path.read_text()
    if PI_VERTEX_BROKEN in text:
        if check_only:
            print(f"would patch: {path} (pi-vertex issue #2)")
            return
        path.write_text(text.replace(PI_VERTEX_BROKEN, PI_VERTEX_FIXED))
        print(f"patched: {path} (pi-vertex issue #2)")
    elif PI_VERTEX_FIXED not in text:
        print(
            f"WARNING: {path} matches neither the issue #2 pattern nor our "
            "patch — upstream may have changed; check pi-vertex releases"
        )


def pi_symlink_map() -> dict[Path, Path]:
    """dest in ~/.pi/agent -> source file in the repo.

    Extensions link individually (like claude agents/commands/hooks) so
    the runtime-written *.config.json files stay unmanaged next to them;
    pi-vertex extension settings link 1:1. Backups must go under
    PI_HOME/backups — a .bak next to an extension would be discovered.
    """
    links: dict[Path, Path] = {}
    for sub in ("extensions", "settings"):
        for src in sorted((REPO / "pi" / sub).iterdir()):
            if src.is_file():
                links[PI_HOME / sub / src.name] = src
    return links


def install_pi(servers: dict, config: dict, check_only: bool) -> bool:
    """Render pi/ templates → ~/.pi/agent: models.json (EnMaaS providers
    + the carried-over litellm proxy, apiKey via !cat at request time),
    settings.json (drift-aware — pi itself writes theme, packages,
    changelog version there), mcp.json (shared pi-family translation;
    needs pi 1.x), AGENTS.md (common guidelines), and 1:1 symlinks for
    extensions + pi-vertex settings (gathered from the pre-repo setup)."""
    write_if_changed(PI_HOME / "AGENTS.md", instructions_text("pi"), check_only)
    for dest, source in pi_symlink_map().items():
        link(dest, source, check_only, root=PI_HOME)
    patch_pi_vertex(check_only)
    models = JINJA.get_template("pi/models.json.j2").render(home=str(HOME))
    try:
        json.loads(models)
    except ValueError as e:
        print(f"INVALID: pi/models.json.j2 — {e}", file=sys.stderr)
        return False
    print("valid: pi/models.json.j2")
    write_if_changed(
        PI_HOME / "models.json", models, check_only, parse=json.loads
    )
    settings = JINJA.get_template("pi/settings.json.j2").render(
        enmaas_enabled=config["enmaas"]["enabled"]
    )
    try:
        new = json.loads(settings)
    except ValueError as e:
        print(f"INVALID: pi/settings.json.j2 — {e}", file=sys.stderr)
        return False
    print("valid: pi/settings.json.j2")

    def drift_warn(old: dict, backup_path: Path | None) -> None:
        warn_drift(
            "~/.pi/agent/settings.json", "pi/settings.json.j2", old, new, backup_path
        )

    write_if_changed(
        PI_HOME / "settings.json",
        settings,
        check_only,
        root=PI_HOME,
        parse=json.loads,
        warn=drift_warn,
    )
    write_if_changed(
        PI_HOME / "mcp.json", pi_mcp_json(servers, "pi"), check_only, parse=json.loads
    )
    return True


# --- harnesses/ --------------------------------------------------------------
#
# A harness is a task-scoped agent: its own config dir, instructions, MCP
# servers, model and tool policy — e.g. a Slack-only assistant started from a
# T3 Code thread or as `~/.pi/harnesses/slack/pi -p ...`.
#
# harnesses/<name>/ holds the task content (harness.jsonc, AGENTS.md, skills/);
# the agent's *format* templates live in <agent>/harness/. pi is the only agent
# with a harness renderer so far: PI_CODING_AGENT_DIR (pi 1.x) points the whole
# pi config dir at ~/.pi/harnesses/<name>, and T3 Code's pi provider accepts any
# executable as its binary path, so the generated launcher is both the CLI entry
# point and the T3 provider binary.

HARNESSES_SRC = REPO / "harnesses"
PI_HARNESSES = PI_HOME.parent / "harnesses"
HARNESS_AGENTS = ("pi",)


def load_harnesses() -> list[dict]:
    """Load and sanity-check harnesses/*/harness.jsonc."""
    harnesses = []
    for manifest in sorted(HARNESSES_SRC.glob("*/harness.jsonc")):
        name = manifest.parent.name
        with open(manifest) as f:
            data = json5_loads(f.read())
        if data.get("agent") not in HARNESS_AGENTS:
            raise ValueError(
                f"{manifest}: agent must be one of {HARNESS_AGENTS}, "
                f"got {data.get('agent')!r}"
            )
        model = data.get("model")
        if not isinstance(model, dict) or not {"provider", "id"} <= model.keys():
            raise ValueError(f"{manifest}: model needs provider and id")
        mcp = data.get("mcp")
        if not isinstance(mcp, dict) or not mcp:
            raise ValueError(f"{manifest}: mcp must list at least one server")
        harnesses.append({"name": name, **data})
    return harnesses


def install_harnesses(servers: dict, config: dict, check_only: bool) -> bool:
    """Render each harness into its agent's harness location. Task content
    comes from harnesses/<name>/, formats from <agent>/harness/."""
    try:
        harnesses = load_harnesses()
    except ValueError as e:
        print(f"INVALID: harnesses — {e}", file=sys.stderr)
        return False
    ok = True
    for harness in harnesses:
        if harness["agent"] == "pi":
            ok &= install_pi_harness(harness, servers, check_only)
    return ok


def install_pi_harness(harness: dict, servers: dict, check_only: bool) -> bool:
    """~/.pi/harnesses/<name>: rendered mcp.json + settings.json, symlinked
    AGENTS.md/models.json/skills, and the executable `pi` launcher."""
    name = harness["name"]
    dest = PI_HARNESSES / name
    selected = {}
    for server_name, entry in harness["mcp"].items():
        entry = entry or {}
        if "type" in entry:
            # Harness-owned definition, for a server no other agent should
            # have. Validated exactly like an mcp/servers.jsonc entry.
            try:
                jsonschema.validate({server_name: entry}, mcp_server_schema())
            except jsonschema.ValidationError as e:
                print(
                    f"INVALID: harnesses/{name}/harness.jsonc — {server_name}: "
                    f"{e.message}",
                    file=sys.stderr,
                )
                return False
            selected[server_name] = entry
        elif server_name in servers:
            selected[server_name] = deep_merge(servers[server_name], entry)
        else:
            print(
                f"INVALID: harnesses/{name}/harness.jsonc — unknown MCP server "
                f"{server_name!r} (define it with a `type` or add it to "
                "mcp/servers.jsonc)",
                file=sys.stderr,
            )
            return False
    write_if_changed(
        dest / "mcp.json", pi_mcp_json(selected, "pi"), check_only, parse=json.loads
    )

    model = {"thinking": "medium", **harness["model"]}
    settings = JINJA.get_template("pi/harness/settings.json.j2").render(model=model)
    try:
        new = json.loads(settings)
    except ValueError as e:
        print(f"INVALID: pi/harness/settings.json.j2 — {e}", file=sys.stderr)
        return False
    write_if_changed(
        dest / "settings.json",
        settings,
        check_only,
        root=dest,
        parse=json.loads,
        warn=lambda old, backup: warn_drift(
            f"~/.pi/harnesses/{name}/settings.json",
            "pi/harness/settings.json.j2",
            old,
            new,
            backup,
        ),
    )

    args = ["--no-skills"]
    if harness.get("excludeTools"):
        args += ["--exclude-tools", ",".join(harness["excludeTools"])]
    skills_src = HARNESSES_SRC / name / "skills"
    if skills_src.is_dir():
        for skill in sorted(p for p in skills_src.iterdir() if p.is_dir()):
            link(dest / "skills" / skill.name, skill, check_only, root=dest)
            args += ["--skill", str(dest / "skills" / skill.name)]
    for rel in harness.get("extensions", []):
        source = HARNESSES_SRC / name / rel
        if not source.is_file():
            print(
                f"INVALID: harnesses/{name}/harness.jsonc — missing extension "
                f"{rel!r}",
                file=sys.stderr,
            )
            return False
        link(dest / rel, source, check_only, root=dest)
        args += ["--extension", str(dest / rel)]

    env = harness.get("env", {})
    for key, value in env.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or not (
            value is None or isinstance(value, str)
        ):
            print(
                f"INVALID: harnesses/{name}/harness.jsonc — env {key!r} needs a "
                "shell variable name and a string (or null to pass it through)",
                file=sys.stderr,
            )
            return False
    env_exports = "\n".join(
        f'export {key}="${{{key}:-}}"'
        if value is None
        else f"export {key}={shlex.quote(value)}"
        for key, value in env.items()
    )
    launcher = JINJA.get_template("pi/harness/launcher.j2").render(
        home=str(HOME),
        args=[shlex.quote(arg) for arg in args],
        env_exports=env_exports,
    )
    write_if_changed(dest / "pi", launcher, check_only)
    if not check_only:
        (dest / "pi").chmod(0o700)

    link(dest / "AGENTS.md", HARNESSES_SRC / name / "AGENTS.md", check_only, root=dest)
    for rel in harness.get("files", []):
        source = HARNESSES_SRC / name / rel
        if not source.is_file():
            print(
                f"INVALID: harnesses/{name}/harness.jsonc — missing file {rel!r}",
                file=sys.stderr,
            )
            return False
        link(dest / rel, source, check_only, root=dest)
    # One provider set for every pi config dir; the harness narrows the model
    # in settings.json. Depends on the pi section having rendered models.json.
    link(dest / "models.json", PI_HOME / "models.json", check_only, root=dest)
    return True


# --- main --------------------------------------------------------------------


INSTALLERS = (
    install_shared,
    install_opencode,
    install_claude,
    install_codex,
    install_omp,
    install_pi,
    install_harnesses,
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
        "--plan",
        action="store_true",
        help="like --check, plus a unified diff of every file that would change",
    )
    parser.add_argument(
        "--plugins",
        action="store_true",
        help="only claude plugins (incl. the mcp plugin)",
    )
    parser.add_argument(
        "agent",
        nargs="?",
        choices=[
            f.__name__[len("install_"):]
            for f in INSTALLERS
            if f.__name__ != "install_shared"  # internal, not an agent
        ],
        help="install only this agent (default: all)",
    )
    return parser.parse_args()


def main() -> int:
    global PLAN
    args = parse_args()
    PLAN = args.plan
    check_only = args.check or args.plan
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
        return 0 if install_claude_plugins(servers, config, check_only) else 1
    selected = [
        f for f in INSTALLERS if not args.agent or f.__name__ == f"install_{args.agent}"
    ]
    ok = True
    for installer in selected:
        try:
            ok &= installer(servers, config, check_only)
        except Exception as e:  # one agent failing must not abort the others
            ok = False
            print(f"FAILED: {installer.__name__} — {e!r}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
