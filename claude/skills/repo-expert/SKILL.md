---
name: repo-expert
description: Discover, cache, and query repository knowledge. Builds modular cache of repo structure, architecture, patterns, and conventions to avoid repeated full-discovery. Use when asking about a repo's internals, architecture, conventions, or when first exploring a new codebase.
allowed-tools: Read, Bash, Grep, Glob, Write, Edit, ToolSearch
---

# Repo Expert — Cached Repository Knowledge

Build and maintain a modular knowledge cache for code repositories, avoiding token-expensive full-discovery on every conversation.

## Cache Location

Store cache at `<repo-root>/.claude/cache.local/repo-expert/`.

When working in a **git worktree**, resolve the main worktree first and use its cache:

```bash
main_worktree=$(git worktree list | head -1 | awk '{print $1}')
cache_dir="${main_worktree}/.claude/cache.local/repo-expert"
```

Always read from and write to the main worktree cache — single source of truth across all worktrees.

## Cache Structure

```
.claude/cache.local/repo-expert/
├── _index.md           # Topic manifest + quick lookup table
├── codebase-map.md     # Annotated directory tree + package summary
├── architecture.md     # High-level structure, entry points, layering
├── build-and-ci.md     # Build system, CI config, how to test/lint/build
├── dependencies.md     # Key deps, versions, purpose
├── api-surface.md      # Public APIs, endpoints, CLI commands, interfaces
├── data-models.md      # Core types, schemas, DB models, CRDs
├── patterns.md         # Conventions, error handling, logging, naming
├── testing.md          # Test framework, how to run, coverage, fixtures
└── <custom-topic>.md   # Additional topics discovered during analysis
```

### File Format

Every cache file uses this frontmatter:

```markdown
---
topic: <topic-slug>
last_commit: <short-sha>
last_updated: <YYYY-MM-DD>
scope: <glob or directory list this topic covers>
---

<content>
```

### Index File (_index.md)

The index is the only file loaded on startup. It contains:

```markdown
---
repo: <repo-name>
repo_root: <absolute-path>
last_full_scan: <short-sha>
last_updated: <YYYY-MM-DD>
---

# Repo Expert Cache: <repo-name>

## One-Line Summary
<What this repo is and does, in one sentence.>

## Topics

| Topic | File | Summary | Last Commit |
|-------|------|---------|-------------|
| Architecture | architecture.md | Monorepo, Go, 3 main packages | abc1234 |
| Build & CI | build-and-ci.md | Make + Prow, `make test` | abc1234 |
| ... | ... | ... | ... |

## Quick Lookup

| Pattern/Concept | Location | Notes |
|-----------------|----------|-------|
| Main entry point | cmd/main.go | cobra CLI |
| DB migrations | pkg/db/migrations/ | goose |
| ... | ... | ... |
```

## Workflow

### On Invocation

1. **Resolve cache location** — find main worktree, set `cache_dir`
2. **Check if cache exists** — read `_index.md` if present
3. **If no cache** — run full discovery (see below), create all topic files
4. **If cache exists** — check staleness, then answer the question

### Full Discovery (First Run)

Run these in sequence:

1. **Repo basics**: read README, go.mod/package.json/Cargo.toml/pyproject.toml, Makefile, Dockerfile
2. **Directory structure**: `find . -type f -not -path '*/vendor/*' -not -path '*/node_modules/*' -not -path '*/.git/*' | head -500` + directory tree
3. **Codebase map**: build annotated directory tree (see below)
4. **Architecture**: identify main packages/modules, entry points, layering
5. **Build system**: Makefile targets, CI config files, scripts/
6. **Dependencies**: parse dependency files, note key frameworks
7. **API surface**: grep for route registration, handler definitions, CLI commands, exported interfaces
8. **Data models**: find struct/class/type definitions, schemas, migrations
9. **Patterns**: sample 5-10 representative files for conventions (error handling, logging, naming)
10. **Testing**: find test files, test configs, identify test framework

After discovery, write each topic to its own file with frontmatter, then write `_index.md`.

### Codebase Map (codebase-map.md)

An annotated directory tree (2-3 levels deep) with purpose notes per directory. Acts as a routing layer between the high-level `architecture.md` and raw file lookups.

**Format:**

```markdown
---
topic: codebase-map
last_commit: <short-sha>
last_updated: <YYYY-MM-DD>
scope: "*"
---

# Codebase Map

## Directory Tree

cmd/                        # CLI entry points
  server/                   # Main API server binary
  migrate/                  # DB migration tool
pkg/
  api/                      # REST handlers, routes, middleware
  controllers/              # Reconciliation loops
  models/                   # Core domain types, DB models
  store/                    # Storage layer (SQL, cache)
internal/
  auth/                     # Token validation, RBAC
  metrics/                  # Prometheus instrumentation
config/                     # YAML configs, env-specific overrides
deploy/                     # Helm charts, Kustomize, ArgoCD
scripts/                    # Dev/CI helper scripts
docs/                       # Architecture docs, ADRs
```

**For Go repos** — add a package summary section:

```markdown
## Go Packages

| Package | Purpose | Key Exports | Files |
|---------|---------|-------------|-------|
| pkg/api | REST handlers | Handler, Router, Middleware | 12 |
| pkg/store | Storage layer | Store (interface), SQLStore | 8 |
| pkg/models | Domain types | Cluster, Node, Config | 6 |
| internal/auth | AuthN/AuthZ | Validator, RBACCheck | 4 |
```

Generate the package summary by scanning Go files for `package` declarations and exported symbols. If gopls LSP is available, prefer it for accurate symbol resolution.

**LSP Bootstrap:** The LSP tool is deferred. Before first use, call `ToolSearch("select:LSP")` to load its schema. Key operations for Go repos:
- `workspaceSymbol(query)` — find symbols by name across workspace
- `documentSymbol(file)` — list all symbols in a file
- `goToDefinition` / `findReferences` — navigate symbol graph
- `goToImplementation` — find interface implementors
- `hover` — get type info and docs
- `incomingCalls` / `outgoingCalls` — call hierarchy

**For non-Go repos** — skip the package summary, keep the annotated directory tree only.

Record current HEAD commit:
```bash
git rev-parse --short HEAD
```

### Staleness Check

When cache exists, check freshness before answering:

```bash
current=$(git rev-parse --short HEAD)
cached=$(grep 'last_commit:' _index.md | head -1 | awk '{print $2}')
```

If different, check what changed:
```bash
git diff --name-only <cached_commit>..HEAD
```

Then decide which topics need refresh based on changed file paths vs each topic's `scope:` field. Only refresh stale topics — don't redo full discovery.

### Answering Questions

1. Read `_index.md` (always — it's small)
2. Identify which topic(s) are relevant to the question
3. Read only those topic files
4. If topic file is stale for this question, refresh it first
5. If question spans something not cached, investigate and create a new topic file
6. Answer with file paths and line references where useful

### Cache Updates

When refreshing a topic:
1. Re-investigate that area of the codebase
2. Overwrite the topic file with updated content and new `last_commit:`
3. Update the corresponding row in `_index.md`

When adding a new topic:
1. Create `<topic-slug>.md` with proper frontmatter
2. Add row to the Topics table in `_index.md`
3. Add any relevant entries to Quick Lookup table

## Content Guidelines

Cache files should contain:
- **Facts about the repo** — structure, patterns, conventions, key files
- **Locations** — file paths, line ranges for important definitions
- **Relationships** — how modules connect, dependency flow
- **"How to" shortcuts** — build commands, test commands, common operations

Cache files should NOT contain:
- Ongoing work or task state
- Git history or recent changes (use `git log` for that)
- Full file contents (just summarize + point to location)
- Opinions or recommendations

## Gitignore

Remind user to add to `.gitignore` if not already present:
```
.claude/cache.local/
```

## Output Format

When answering a question:
- State whether answer came from cache or fresh investigation
- If from cache, note the commit it was cached at
- Include file paths with line numbers where relevant
- If cache was updated during this answer, mention it
