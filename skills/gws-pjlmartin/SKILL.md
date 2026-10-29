---
name: gws-pjlmartin
description: "gws-pjlmartin: Access personal Google Workspace docs/sheets via serviceaccount."
metadata:
  version: 1.0.0
  openclaw:
    category: "productivity"
    requires:
      bins:
        - gws-pjlmartin
    cliHelp: "gws-pjlmartin --help"
date: 2026-08-24
---

# gws-pjlmartin

Wrapper script for `gws` with environment pre-configured to access personal Google Workspace docs, sheets, and Drive files in the `pjlmartin` Google Cloud project using the `gws-bot` service account.

## Setup

The `gws-pjlmartin` script is in your PATH and automatically sets:
- `GOOGLE_APPLICATION_CREDENTIALS` → service account JSON
- `GOOGLE_CLOUD_PROJECT` → `pjlmartin`
- Other auth/config flags for the gws binary

## Usage

Use exactly like `gws`, but with automatic pjlmartin project/service account context:

```bash
gws-pjlmartin sheets <resource> <method> [flags]
gws-pjlmartin docs <resource> <method> [flags]
gws-pjlmartin drive <resource> <method> [flags]
```

## Helper Skills

Since gws-pjlmartin is a transparent wrapper, use the underlying GWS skills:

- [`gws-sheets`](../gws-sheets/SKILL.md) — Read and write spreadsheets
- [`gws-docs`](../gws-docs/SKILL.md) — Read and write Google Docs
- [`gws-drive`](../gws-drive/SKILL.md) — Manage files, folders, and shared drives

## Auth & Security

Authentication handled automatically by the `gws-pjlmartin` script using the `gws-bot@pjlmartin.iam.gserviceaccount.com` service account. No credential files to manage.

The service account has access to resources explicitly shared with it. If you get a "not found" or "permission denied" error, grant the service account access to the specific Doc/Sheet/Drive resource:

```
gws-bot@pjlmartin.iam.gserviceaccount.com
```

## Example Commands

```bash
# List spreadsheets accessible to gws-bot
gws-pjlmartin sheets spreadsheets list

# Read a sheet by ID
gws-pjlmartin sheets values get --spreadsheetId <ID> --range A1:Z10

# Create a new Doc
gws-pjlmartin docs documents create --title "My Doc"

# List files in Drive
gws-pjlmartin drive files list
```

If you see "not found" or permission errors, make sure the Doc/Sheet/Drive resource is shared with `gws-bot@pjlmartin.iam.gserviceaccount.com`.

## Discovery

Inspect available methods and required params:

```bash
gws-pjlmartin sheets --help
gws-pjlmartin docs --help
gws-pjlmartin drive --help

# Check schema for a specific method
gws-pjlmartin schema sheets.<resource>.<method>
```
