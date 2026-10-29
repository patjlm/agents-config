---
name: prow-job-search
description: Search OpenShift Prow CI logs across jobs to find failure patterns, check if a job failure is isolated or widespread, and gauge blast radius/timing of an outage. Use when investigating a Prow job failure, checking if others hit the same error, or asking "is this widespread" / "what are the latest fails".
---

Use `search.dptools.openshift.org` (mirror of `search.ci.openshift.org`) to grep across Prow job logs/junit results. The site is a JS SPA — do NOT fetch the bare root URL and expect results. Always construct the full query URL directly; webfetch on that URL returns the rendered results as text.

## URL pattern

```
https://search.dptools.openshift.org/?search=<url-encoded-regex>&maxAge=<window>&context=<lines>&type=<junit|build-log|all>&name=&excludeName=&maxMatches=5&maxBytes=20971520&groupBy=job
```

Key params:
- `search` — ripgrep regex, URL-encoded (e.g. `failed+to+get+integrated+stream` or `ocp%2F5.2` for `ocp/5.2`)
- `maxAge` — time window: `6h`, `12h`, `1d`, `2d`, `7d`, `14d` (longer windows are slower/more likely to time out)
- `type` — `junit` (fast, structured test failures — prefer this first), `build-log` (raw logs, slower/heavier), `all`
- `context` — lines of context around match (0-15)
- `groupBy=job` — groups results by job name with %failed and %impact, much easier to scan than flat list
- `name` / `excludeName` — regex filter on job name

## Workflow

1. Start narrow: `type=junit`, `maxAge=6h` or `24h`, `groupBy=job`. `type=build-log` with long `maxAge` windows frequently 504s (server-side, not a client issue) — if that happens, narrow the search string or shrink `maxAge` rather than retrying the same query.
2. Read the summary line at bottom of results: `Found in X% of runs (Y% of failures) across N total runs and M jobs (Z% failed)` — gives quick sense of blast radius.
3. Per-job blocks show `N runs, X% failed, Y% of failures match = Z% impact` — 100% impact across many unrelated jobs/repos = systemic outage, not a one-off.
4. Output is large for broad queries — expect truncation. If truncated and you need to process the whole thing, delegate to an agent to grep the saved tool-output file rather than reading it all inline.
5. For "is this the same as ticket/thread X" — search the exact error substring, not the general symptom, to avoid false positives from superficially similar but distinct failures (e.g. configresolver `integratedStream` errors vs `clusterProfile` errors are different code paths even though both come from `ci-operator-configresolver`).

## Getting the specific failing job's raw logs

For a single job's own failure (not searching across jobs), fetch directly instead of using search:
```
https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/<path-from-prow-url>/build-log.txt
https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/<path-from-prow-url>/finished.json
```
The `prow.ci.openshift.org/view/...` URL itself is a JS SPA and won't render logs via webfetch — always go straight to gcsweb for raw artifacts.
