/**
 * Gemini thought-signature replay for EnMaaS.
 *
 * EnMaaS serves Gemini models only over the OpenAI-compatible
 * /v1/chat/completions route, and Vertex rejects a function call whose
 * assistant message does not carry the call's thought_signature
 * ("400 … Function call is missing a thought_signature in functionCall
 * parts"). The signature arrives in the response as
 * `tool_calls[].extra_content.google.thought_signature` — a field pi's
 * openai-completions provider does not keep, so the second turn of any tool
 * loop fails.
 *
 * This extension closes the loop: it records each signature from the raw
 * provider stream and puts it back on the matching assistant tool call in
 * the outgoing payload. When no signature was captured (an older session, a
 * replayed branch, a mangled tool-call id) it falls back to Google's
 * documented bypass value, which Vertex accepts but without the model's
 * thought context.
 *
 * Scope: only the enmaas-google provider / gemini-* models. Other providers
 * are untouched, and a payload without tool calls is returned unchanged.
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

/** Accepted by Vertex in place of a real thought_signature (no thought context). */
const DUMMY_SIGNATURE = "skip_thought_signature_validator";

/** Signatures captured from the stream, keyed by tool-call id. */
const signatures = new Map<string, string>();

interface ToolCallLike {
	id?: unknown;
	extra_content?: { google?: { thought_signature?: unknown } };
}

function isGemini(ctx: ExtensionContext): boolean {
	const model = ctx.model;
	if (!model) return false;
	return model.provider === "enmaas-google" || /(^|\/)gemini-/.test(model.id ?? "");
}

function toolCalls(value: unknown): ToolCallLike[] {
	return Array.isArray(value) ? (value as ToolCallLike[]) : [];
}

/** Record signatures from a /v1/chat/completions stream chunk. */
function capture(data: unknown): void {
	const choices = (data as { choices?: unknown } | null)?.choices;
	if (!Array.isArray(choices)) return;
	for (const choice of choices as Record<string, unknown>[]) {
		const delta = (choice.delta ?? choice.message) as Record<string, unknown> | undefined;
		for (const call of toolCalls(delta?.tool_calls)) {
			const signature = call.extra_content?.google?.thought_signature;
			if (typeof signature === "string" && typeof call.id === "string") {
				signatures.set(call.id, signature);
			}
		}
	}
}

/** Put a signature back on every assistant tool call that lacks one. */
function patch(payload: unknown): unknown {
	const messages = (payload as { messages?: unknown } | null)?.messages;
	if (!Array.isArray(messages)) return undefined;
	let patched = false;
	for (const message of messages as Record<string, unknown>[]) {
		for (const call of toolCalls(message?.tool_calls)) {
			if (call.extra_content?.google?.thought_signature) continue;
			const known = typeof call.id === "string" ? signatures.get(call.id) : undefined;
			call.extra_content = {
				...call.extra_content,
				google: { thought_signature: known ?? DUMMY_SIGNATURE },
			};
			patched = true;
		}
	}
	return patched ? payload : undefined;
}

export default function geminiThoughtSignature(pi: ExtensionAPI) {
	// A fresh session starts a fresh transcript; stale ids would only add noise.
	pi.on("session_start", () => signatures.clear());
	pi.on("provider_stream_event", (event) => capture(event.data));
	pi.on("before_provider_request", (event, ctx) => {
		if (!isGemini(ctx)) return;
		return patch(event.payload);
	});
}
