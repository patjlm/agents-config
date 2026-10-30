/**
 * Slack harness guard.
 *
 * The harness runs with T3 approvals off and Slack content is untrusted, so
 * this extension enforces a narrow policy on every tool call (including calls
 * a codemode script makes through ctx.executeTool — those carry
 * `parentToolCallId` and hit this same hook):
 *
 *   bash          only ~/.pi/harnesses/slack/slack.py, no shell chaining
 *   read          only /tmp/slack-image-* (the helper's image downloads)
 *   edit / write  never
 *   codemode      allowed: QuickJS sandbox with no fs/net, nested calls checked
 *   tool_search   allowed (it only declares tools)
 *   MCP tools     read-onlyHint tools allowed; everything else needs approval
 *   other tools   needs approval
 *
 * With no UI (print/JSON modes) there is nobody to approve: such calls are
 * blocked, except Slack MCP write tools when the caller sets
 * SLACK_HARNESS_ALLOW_WRITES=1.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const HOME = process.env.HOME ?? "";
const HELPER = `${HOME}/.pi/harnesses/slack/slack.py`;
const IMAGE_PREFIX = "/tmp/slack-image-";
const ALLOW_WRITES = process.env.SLACK_HARNESS_ALLOW_WRITES === "1";

/** Shell-safe for the helper's arguments: metacharacters only inside quotes. */
function argsSafe(rest: string): boolean {
	let quote: "'" | '"' | null = null;
	for (let i = 0; i < rest.length; i++) {
		const ch = rest[i]!;
		if (quote === "'") {
			if (ch === "'") quote = null;
			continue;
		}
		if (quote === '"') {
			if (ch === "\\") i++;
			else if (ch === '"') quote = null;
			else if (ch === "`" || (ch === "$" && rest[i + 1] === "(")) return false;
			continue;
		}
		if (ch === "'" || ch === '"') {
			quote = ch;
			continue;
		}
		if (";&|`$<>\n".includes(ch)) return false;
	}
	return quote === null;
}

function helperCommand(command: string): boolean {
	const match = /^(\S+)(.*)$/s.exec(command.trim());
	if (match === null) return false;
	const [, executable, rest] = match;
	const expanded = executable!
		.replace(/^~(?=\/)/, HOME)
		.replace(/^\$\{HOME\}(?=\/)/, HOME)
		.replace(/^\$HOME(?=\/)/, HOME);
	return expanded === HELPER && argsSafe(rest ?? "");
}

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event, ctx) => {
		const name = event.toolName;

		const approve = async (label: string): Promise<boolean> => {
			if (!ctx.hasUI) return false;
			return ctx.ui.confirm(
				`Allow ${label}?`,
				"Outside the Slack harness policy.",
			);
		};

		if (name === "bash") {
			if (helperCommand(String(event.input.command ?? ""))) return;
			if (await approve("bash")) return;
			return {
				block: true,
				reason:
					"Only the Slack helper may run: ~/.pi/harnesses/slack/slack.py …",
			};
		}

		if (name === "read") {
			if (String(event.input.path ?? "").startsWith(IMAGE_PREFIX)) return;
			if (await approve("read")) return;
			return {
				block: true,
				reason: "Only Slack images under /tmp/slack-image-* may be read.",
			};
		}

		if (name === "edit" || name === "write") {
			return { block: true, reason: "This harness does not modify files." };
		}

		if (name === "codemode" || name === "tool_search") return;

		const annotations = pi
			.getAllTools()
			.find((tool) => tool.name === name)?.annotations;
		if (annotations?.readOnlyHint === true) return;

		if (!ctx.hasUI && ALLOW_WRITES && name.startsWith("mcp__slack_mcp__")) {
			return;
		}
		if (await approve(name)) return;
		return {
			block: true,
			reason: `${name} needs approval and none is available here.`,
		};
	});
}
