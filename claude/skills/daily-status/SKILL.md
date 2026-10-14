---
name: daily-status
description: Gather activity from GitHub, Jira, and Slack to produce a reconciled daily status summary with cross-linked references
argument-hint: [hours-back] [slack-channel-id]
disable-model-invocation: true
allowed-tools: Bash(gh *), mcp__plugin_atlassian_atlassian__atlassianUserInfo, mcp__plugin_atlassian_atlassian__searchJiraIssuesUsingJql, mcp__slack-mcp__search_messages, mcp__slack-mcp__get_channel_history, mcp__slack-mcp__whoami, mcp__slack-mcp__post_message
---

# Daily Status

Generate a reconciled activity summary by gathering data from GitHub, Jira, and Slack, then merging related items into unified bullet points with cross-linked references.

## Arguments

- `$0` (optional): Hours to look back. Defaults to `24`.
- `$1` (optional): Slack channel ID to search for discussions. If omitted, Slack search uses keyword matching across all channels.
- `$2` (optional): Slack thread URL to post the status to (e.g. `https://redhat-internal.slack.com/archives/C09BP1TBB2T/p1780566542188049?thread_ts=1780560902.512279&cid=C09BP1TBB2T`).

## Step 1: Gather identities

Run these in parallel:
- Get GitHub username: `gh api user --jq '.login'`
- Get Jira identity: `mcp__plugin_atlassian_atlassian__atlassianUserInfo`
- Get Slack identity: `mcp__slack-mcp__whoami`

## Step 2: Gather raw activity

Run all of these in parallel using the identities from Step 1.

### GitHub

Use `gh` CLI. Compute the date threshold from `$0` (default 24) hours ago.

1. **My PRs** (authored, updated in window):
   ```
   gh search prs --author=<username> --updated=">=$(date threshold)" --json title,url,state,updatedAt,repository
   ```

2. **All PRs I'm involved in** (reviewed, commented, requested — not authored). This captures open, merged, and closed PRs:
   ```
   gh api "search/issues?q=involves:<username>+type:pr+updated:>=$(date threshold)+-author:<username>&sort=updated&order=desc&per_page=30" --jq '.items[] | {title, url: .html_url, state, updated_at}'
   ```
   For each result, check actual merge state via `gh pr view --repo <owner/repo> <number> --json state,mergedAt` to distinguish open vs merged vs closed.

3. **My commits** on active branches (optional, for context):
   ```
   gh api "search/commits?q=author:<username>+author-date:>=$(date threshold)&sort=author-date&order=desc&per_page=20" -H 'Accept: application/vnd.github.cloak-preview+json' --jq '.items[] | {message: .commit.message, repo: .repository.full_name, sha: .sha[0:7], url: .html_url}'
   ```

4. **GitHub issues I'm involved in** (commented, authored, updated in window):
   ```
   gh api "search/issues?q=involves:<username>+type:issue+updated:>=$(date threshold)&sort=updated&order=desc&per_page=20" --jq '.items[] | {title, url: .html_url, state, number, updated_at, repo: (.repository_url | split("/") | .[-2:] | join("/"))}'
   ```
   For each result, verify actual activity by the user (comments in window) before including:
   ```
   gh api repos/<owner>/<repo>/issues/<number>/comments --jq '[.[] | select(.user.login == "<username>" and .updated_at >= "<date threshold>") | {body: .body[:120], created_at: .created_at}]'
   ```
   Only include issues where the user actually commented or created them in the window — not issues that merely updated due to external activity.

### Jira

Use the Jira account_id from Step 1.

1. **Issues I updated** (assigned or reporter, updated in window):
   ```
   searchJiraIssuesUsingJql with:
   jql: "(assignee = '<account_id>' OR reporter = '<account_id>') AND updated >= -<hours>h ORDER BY updated DESC"
   fields: ["summary", "status", "issuetype", "priority", "updated"]
   cloudId: "https://issues.redhat.com"
   maxResults: 30
   ```

2. Extract each issue's key, summary, status, and web URL.

### Slack

Search Slack for messages by the current user in the time window. Use keyword matching against Jira issue keys and PR numbers found in Steps above to find relevant discussions.

1. For each Jira issue key (e.g., `GCP-479`) and PR number found, search:
   ```
   mcp__slack-mcp__search_messages with query: "<issue-key>" or "PR #<number>"
   ```
   No hard cap on total searches — cover every Jira key and notable PR/theme found in Steps above. Prioritize Jira keys first (more likely discussed in Slack). Pace the calls: run in small batches (3-4 at a time) rather than all at once, to avoid rate-limiting.

2. If `$1` (channel ID) is provided, also fetch recent channel history:
   ```
   mcp__slack-mcp__get_channel_history with channel_id: $1, limit: 50
   ```
   Filter for messages from the current user or mentioning relevant issue keys.

**Important**: If any API call fails, fix the command and retry immediately. Do NOT silently skip failed data sources — each source may reveal work items the others miss.

## Step 3: Reconcile and merge

This is the critical step. Do NOT just list items per source or per status. Instead, group by **theme** — the logical initiative, feature area, or project that ties work items together.

1. **Match work items across sources**: Link GitHub PRs to Jira issues using:
   - PR title/branch containing Jira key (e.g., `GCP-479` in `fix(gcp-479): ...`)
   - PR body referencing Jira URLs
   - Jira description referencing PR URLs
   - Slack messages mentioning either

2. **Identify themes**: Cluster related work items into themes based on shared Jira epics/initiatives, repository, feature area, or logical project. Good themes are 2-5 word labels (e.g., "GitOps Promoter", "Roger Flores Onboarding", "Config Connector & Renovate"). Aim for 3-7 themes — merge small items into broader themes rather than creating single-bullet sections.

3. **Write narrative status per theme**: For each theme, write 1-3 sentences describing what happened and what's next. This is a topic status update, NOT a listing of PR/Jira actions. Focus on progress, decisions made, and next steps. Append all related links (Jira issues, PRs, Slack threads) as references at the end of the theme block.
   - For PRs by others that I reviewed, verify actual review activity via `gh pr view --repo <owner/repo> <number> --json reviews,comments` before including. Do NOT include PRs that only appear in GitHub's `involves:` filter without actual review/comment activity.

4. **Sort themes**: Most active first.

## Step 4: Format output

Produce a concise summary in this format:

```
## Daily Status - <Name> (<date>)
### Lookback: <hours>h

**<Theme 1>**
<1-3 sentence narrative of what happened, decisions made, and next steps.>
(<jira-full-url> | <pr-full-url> | <slack-full-url>)

**<Theme 2>**
<1-3 sentence narrative.>
(<jira-full-url> | <pr-full-url>)

...
```

Rules:
- Write narrative prose per theme, not bullet lists of actions. Describe the state and progress of the topic.
- Collect all related URLs (Jira, PRs, Slack) in a single parenthesized reference block at the end of each theme.
- Use raw full URLs (e.g. `https://github.com/org/repo/pull/123`), NOT markdown link syntax. The output is often pasted into Slack where markdown links render as just the label text, hiding the URL.
- Use `|` to separate URLs within parentheses.
- Only include Slack URLs when a relevant thread was actually found.
- Sort themes by volume of activity (most active first).
- Do NOT include items outside the time window.

## Step 5: Post to Slack thread

After displaying the status to the user, offer to post it to a Slack thread.

1. If `$2` (thread URL) was provided, parse channel ID and thread_ts from it. Slack thread URLs have format:
   `https://<workspace>.slack.com/archives/<channel_id>/p<timestamp>?thread_ts=<thread_ts>&cid=<channel_id>`
   - Extract `channel_id` from the path (`/archives/<channel_id>/`)
   - Extract `thread_ts` from the query param. If no `thread_ts` param, derive it from the `p<timestamp>` segment: strip the `p` prefix, insert a `.` before the last 6 digits (e.g. `p1780560902512279` → `1780560902.512279`)

2. If no thread URL was provided, ask the user for one.

3. Before posting, reformat the status for Slack mrkdwn:
   - Drop the header lines (name, date, lookback) — redundant in a thread posted by the user
   - Replace `**bold**` with `*bold*`
   - Use Slack link syntax `<url|label>` to shorten URLs while keeping them clickable:
     - Jira: `<https://redhat.atlassian.net/browse/GCP-563|GCP-563>`
     - GitHub PRs: `<https://github.com/openshift-online/gcp-hcp-infra/pull/581|openshift-online/gcp-hcp-infra#581>` (strip `https://github.com/`, replace `/pull/` with `#`)
     - GitHub PRs in `openshift/` org: `<https://github.com/openshift/release/pull/80070|openshift/release#80070>`
   - Use `•` instead of `-` for bullet points
   - Keep it compact — no blank lines between bullets within a section, one blank line between sections

4. Show the formatted message to the user and ask for confirmation before posting.

5. Post using:
   ```
   mcp__slack-mcp__post_message with:
     channel_id: <extracted channel_id>
     thread_ts: <extracted thread_ts>
     message: <formatted status>
   ```
