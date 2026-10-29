---
name: gws-sheets
description: "Google Sheets: Read and write spreadsheets."
metadata:
  version: 0.22.5
  openclaw:
    category: "productivity"
    requires:
      bins:
        - gws
    cliHelp: "gws sheets --help"
source: https://raw.githubusercontent.com/googleworkspace/cli/refs/heads/main/skills/gws-sheets/SKILL.md
date: 2026-08-24
---

# sheets (v4)

> **PREREQUISITE:** Read `../gws-shared/SKILL.md` for auth, global flags, and security rules.

```bash
gws sheets <resource> <method> [flags]
```

## Helper Commands

| Command | Description |
|---------|-------------|
| [`+append`](../gws-sheets-append/SKILL.md) | Append a row to a spreadsheet |
| [`+read`](../gws-sheets-read/SKILL.md) | Read values from a spreadsheet |

## API Resources

### spreadsheets

  - `batchUpdate` — Applies one or more updates to the spreadsheet. Each request is validated before being applied. If any request is not valid then the entire request will fail and nothing will be applied.
  - `create` — Creates a spreadsheet, returning the newly created spreadsheet.
  - `get` — Returns the spreadsheet at the given ID. The caller must specify the spreadsheet ID.
  - `getByDataFilter` — Returns the spreadsheet at the given ID. The caller must specify the spreadsheet ID. Allows selecting which subsets of spreadsheet data to return by specifying a dataFilters parameter.
  - `developerMetadata` — Operations on the 'developerMetadata' resource
  - `sheets` — Operations on the 'sheets' resource
  - `values` — Operations on the 'values' resource

## Discovering Commands

Before calling any API method, inspect it:

```bash
# Browse resources and methods
gws sheets --help

# Inspect a method's required params, types, and defaults
gws schema sheets.<resource>.<method>
```

Use `gws schema` output to build your `--params` and `--json` flags.
