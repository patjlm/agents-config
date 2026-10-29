---
name: gws-docs
description: "Read and write Google Docs."
metadata:
  version: 0.22.5
  openclaw:
    category: "productivity"
    requires:
      bins:
        - gws
    cliHelp: "gws docs --help"
source: https://raw.githubusercontent.com/googleworkspace/cli/refs/heads/main/skills/gws-docs/SKILL.md
date: 2026-08-24
---

# docs (v1)

> **PREREQUISITE:** Read `../gws-shared/SKILL.md` for auth, global flags, and security rules.

```bash
gws docs <resource> <method> [flags]
```

## Helper Commands

| Command | Description |
|---------|-------------|
| [`+write`](../gws-docs-write/SKILL.md) | Append text to a document |

## API Resources

### documents

  - `batchUpdate` — Applies one or more updates to the document. Each request is validated before being applied. If any request is not valid, then the entire request will fail and nothing will be applied.
  - `create` — Creates a blank document using the title given in the request.
  - `get` — Gets the latest version of the specified document.

## Discovering Commands

Before calling any API method, inspect it:

```bash
# Browse resources and methods
gws docs --help

# Inspect a method's required params, types, and defaults
gws schema docs.<resource>.<method>
```

Use `gws schema` output to build your `--params` and `--json` flags.
