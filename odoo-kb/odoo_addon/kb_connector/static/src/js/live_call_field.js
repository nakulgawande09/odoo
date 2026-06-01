/** @odoo-module */
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { Component, onWillDestroy, useState } from "@odoo/owl";

/**
 * LiveCallField — drives a Gemini Live voice session in the Test Console.
 *
 *   Start Call →  /kb/session/live/start  (mints JWT, returns ws_url)
 *               → opens WSS to FastAPI /v1/live/connect
 *               → mic captured via AudioWorklet, sent as 16 kHz Int16 PCM
 *               → model audio (24 kHz PCM) decoded and played via Web Audio
 *               → transcript JSON pushed bubble-by-bubble into conversation_html
 *
 *   End Call   → closes the WS
 *               → /kb/session/live/end  (FastAPI finalize → CRM analysis)
 *               → polls /v1/calls/{uuid} for the call summary panel
 *
 * The transcript continues to live in the existing `conversation_html`
 * field below the widget — same panel users see today. Each turn is
 * appended via the serialized `_appendBubble` chain (carried over from
 * the prior bug-fix work) so writes never race.
 */

const WORKLET_URL =
    "/kb_connector/static/src/js/live_audio_worklet.js";

// Gemini Live emits 24 kHz PCM (model output). Raw PCM, mono, 16-bit LE.
const MODEL_OUTPUT_SAMPLE_RATE = 24000;

export class LiveCallField extends Component {
    static template = "kb_connector.LiveCallField";
    static props = { ...standardFieldProps };

    setup() {
        this.state = useState({
            status: "idle", // idle | starting | listening | thinking | speaking | ended | error
            interim: "",
            errorMessage: "",
            toolCallsInFlight: 0,
        });

        this._ws = null;
        this._micCtx = null;
        this._micNode = null;
        this._micStream = null;
        this._playCtx = null;
        this._playQueueEnd = 0;
        this._greetingAudio = null;
        this._stopping = false;
        this._writeChain = Promise.resolve();
        // Conversation history surfaced to the search_kb tool handler so it
        // can resolve pronouns when the user speaks fragments. The server
        // also keeps its own copy; this is just for any future client-side
        // use (and it's free to maintain).
        this._history = [];

        onWillDestroy(() => {
            this._stopping = true;
            this._teardown();
        });
    }

    // ── Record helpers ───────────────────────────────────────

    get record() {
        return this.props.record;
    }

    get agentId() {
        const ref = this.record.data.agent_id;
        if (ref === null || ref === undefined || ref === false) return null;
        if (Array.isArray(ref)) return ref[0];
        if (typeof ref === "object") return ref.id || ref.resId || null;
        return Number(ref);
    }

    get callUuid() {
        return this.record.data.session_call_uuid || "";
    }

    get isActive() {
        return ["listening", "thinking", "speaking"].includes(this.state.status);
    }

    // ── Button handlers ──────────────────────────────────────

    async onStartCall() {
        if (this.isActive) return;
        if (!this.agentId) {
            this.state.errorMessage = _t("Select a voice agent first");
            this.state.status = "error";
            return;
        }
        if (!globalThis.isSecureContext) {
            this.state.errorMessage = _t(
                "Voice calls require HTTPS. Open Odoo via https:// or http://localhost."
            );
            this.state.status = "error";
            return;
        }

        // Create the Audio element synchronously while the user-gesture token
        // is still valid; we set .src after the bootstrap response arrives.
        this._greetingAudio = new Audio();

        this._stopping = false;
        this._writeChain = Promise.resolve();
        this.state.status = "starting";
        this.state.errorMessage = "";
        await this._clearConversation();

        let bootstrap;
        try {
            bootstrap = await this._jsonRpc("/kb/session/live/start", {
                agent_id: this.agentId,
            });
        } catch (err) {
            this.state.status = "error";
            this.state.errorMessage = `Failed to start session: ${err.message || err}`;
            return;
        }
        if (bootstrap.error) {
            this.state.status = "error";
            this.state.errorMessage = bootstrap.error;
            return;
        }

        await this.record.update({ session_call_uuid: bootstrap.call_uuid });

        try {
            await this._openWebSocket(bootstrap);

            const greeting = (bootstrap.agent_config?.greeting || "").trim();
            if (greeting) {
                this._history.push({ role: "agent", text: greeting });
                await this._appendBubble("agent", greeting);
                await this._playGreetingAudio(greeting);
            }

            await this._startMicrophone();
            this.state.status = "listening";
        } catch (err) {
            this.state.status = "error";
            this.state.errorMessage = String(err && err.message ? err.message : err);
            this._teardown();
        }
    }

    async onEndCall() {
        if (this._stopping) return;
        this._stopping = true;

        // Best-effort: tell the server we're done, close everything, then
        // poll for the finalized record so the call summary panel populates.
        try {
            if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                this._ws.send(JSON.stringify({ type: "end" }));
            }
        } catch (_err) {
            // ignore — closing anyway
        }

        this._teardown();
        this.state.status = "ended";
        this.state.errorMessage = "";

        const uuid = this.callUuid;
        await this.record.update({ session_call_uuid: false });
        if (!uuid) return;

        try {
            const summary = await this._jsonRpc("/kb/session/live/end", {
                call_uuid: uuid,
            });
            if (summary && summary.ready) {
                await this._renderCallSummary(summary);
            }
        } catch (_err) {
            // Non-fatal — user can re-fetch via View Calls
        }
    }

    // ── WebSocket lifecycle ──────────────────────────────────

    async _openWebSocket(bootstrap) {
        const { ws_url, token, call_uuid } = bootstrap;
        if (!ws_url || !token) {
            throw new Error("session/live/start returned an incomplete bootstrap");
        }
        const url = new URL(ws_url);
        url.searchParams.set("token", token);
        url.searchParams.set("agent_id", String(this.agentId));
        url.searchParams.set("call_uuid", call_uuid);

        return new Promise((resolve, reject) => {
            const ws = new WebSocket(url.toString());
            ws.binaryType = "arraybuffer";
            this._ws = ws;

            ws.onopen = () => resolve();
            ws.onerror = (event) => {
                if (ws.readyState !== WebSocket.OPEN) {
                    reject(new Error("WebSocket connection failed"));
                }
                console.warn("LiveCallField: ws error", event);
            };
            ws.onclose = () => {
                if (!this._stopping) {
                    this.state.status = "ended";
                }
            };
            ws.onmessage = (event) => this._handleWsMessage(event);
        });
    }

    async _handleWsMessage(event) {
        if (this._stopping) return;
        if (event.data instanceof ArrayBuffer) {
            this._enqueueAudio(event.data);
            return;
        }
        if (typeof event.data !== "string") return;

        let msg;
        try {
            msg = JSON.parse(event.data);
        } catch (_err) {
            return;
        }
        const type = msg.type || "";

        if (type === "transcript.input.partial") {
            this.state.interim = msg.text || "";
            return;
        }
        if (type === "transcript.input.final") {
            this.state.interim = "";
            const text = (msg.text || "").trim();
            if (text) {
                this._history.push({ role: "caller", text });
                await this._appendBubble("caller", text);
            }
            this.state.status = "thinking";
            return;
        }
        if (type === "transcript.output.partial") {
            // Optional: live caption of the agent's reply. We deliberately
            // don't append a bubble for partials so we don't churn the
            // conversation_html field on every token.
            return;
        }
        if (type === "transcript.output.final") {
            const text = (msg.text || "").trim();
            if (text) {
                this._history.push({ role: "agent", text });
                await this._appendBubble("agent", text);
            }
            this.state.status = "listening";
            return;
        }
        if (type === "tool_call") {
            this.state.toolCallsInFlight = (this.state.toolCallsInFlight || 0) + 1;
            console.debug("LiveCallField: search_kb", msg.query);
            return;
        }
        if (type === "turn_complete") {
            this.state.status = "listening";
            this.state.toolCallsInFlight = 0;
            return;
        }
        if (type === "error") {
            this.state.errorMessage = msg.message || "Live session error";
            this.state.status = "error";
        }
    }

    // ── Microphone capture (16 kHz Int16 PCM via worklet) ────

    async _startMicrophone() {
        let stream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                },
            });
        } catch (err) {
            throw new Error(_t("Microphone permission denied"));
        }
        this._micStream = stream;

        const ctx = new (globalThis.AudioContext || globalThis.webkitAudioContext)();
        this._micCtx = ctx;

        try {
            await ctx.audioWorklet.addModule(WORKLET_URL);
        } catch (err) {
            throw new Error(`Failed to load audio worklet: ${err.message || err}`);
        }

        const src = ctx.createMediaStreamSource(stream);
        const node = new AudioWorkletNode(ctx, "live-pcm-downsampler");
        this._micNode = node;

        node.port.onmessage = (event) => {
            const ws = this._ws;
            if (!ws || ws.readyState !== WebSocket.OPEN) return;
            // event.data is a transferred ArrayBuffer of Int16 PCM samples.
            try {
                ws.send(event.data);
            } catch (_err) {
                // ignore; ws.onclose will tear us down
            }
        };
        src.connect(node);
        // We don't connect to ctx.destination — we only consume mic input.
    }

    // ── Audio playback (Gemini Live emits 24 kHz Int16 PCM) ──

    _enqueueAudio(arrayBuffer) {
        if (!this._playCtx) {
            this._playCtx = new (globalThis.AudioContext ||
                globalThis.webkitAudioContext)({
                sampleRate: MODEL_OUTPUT_SAMPLE_RATE,
            });
            this._playQueueEnd = 0;
        }
        const ctx = this._playCtx;
        const int16 = new Int16Array(arrayBuffer);
        if (int16.length === 0) return;

        const float = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) {
            float[i] = int16[i] / 0x8000;
        }

        const buffer = ctx.createBuffer(1, float.length, MODEL_OUTPUT_SAMPLE_RATE);
        buffer.copyToChannel(float, 0);

        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);

        const startAt = Math.max(ctx.currentTime, this._playQueueEnd);
        source.start(startAt);
        this._playQueueEnd = startAt + buffer.duration;

        // Reflect "speaking" as long as there's queued audio.
        if (!this._stopping) {
            this.state.status = "speaking";
        }
        source.onended = () => {
            if (this._stopping) return;
            if (ctx.currentTime >= this._playQueueEnd - 0.05) {
                if (this.state.status === "speaking") {
                    this.state.status = "listening";
                }
            }
        };
    }

    // ── Greeting playback (one-shot via /kb/tts/speak) ───────

    _playGreetingAudio(text) {
        return new Promise((resolve) => {
            const audio = this._greetingAudio;
            if (!audio || !text || !this.agentId) {
                resolve();
                return;
            }
            this.state.status = "speaking";
            const encoded = encodeURIComponent(text.substring(0, 500));
            audio.src = `/kb/tts/speak/${this.agentId}?text=${encoded}`;
            const done = () => {
                audio.onended = null;
                audio.onerror = null;
                resolve();
            };
            audio.onended = done;
            audio.onerror = (err) => {
                console.warn("LiveCallField: greeting audio failed", err);
                done();
            };
            audio.play().catch((err) => {
                console.warn(
                    "LiveCallField: greeting playback rejected",
                    err && err.message ? err.message : err,
                );
                done();
            });
        });
    }

    // ── Conversation bubble append (serialized writes) ───────

    async _appendBubble(role, text, meta = {}) {
        const next = this._writeChain
            .then(async () => {
                const existing = String(this.record.data.conversation_html || "");
                const bubble = _buildBubble(role, text, meta);
                const updated = _appendToWrapper(existing, bubble);
                await this.record.update({
                    conversation_html: updated,
                    last_response_text:
                        role === "agent" ? text : this.record.data.last_response_text,
                });
            })
            .catch((err) => {
                console.warn("LiveCallField: bubble write failed", err);
            });
        this._writeChain = next;
        return next;
    }

    async _clearConversation() {
        const next = this._writeChain
            .then(async () => {
                await this.record.update({
                    conversation_html:
                        '<div style="overflow:hidden;min-height:60px;"></div>',
                    call_summary_html: false,
                    last_response_text: false,
                });
            })
            .catch((err) => {
                console.warn("LiveCallField: clear conversation failed", err);
            });
        this._writeChain = next;
        return next;
    }

    async _renderCallSummary(summary) {
        const html = _buildSummaryHtml(summary);
        await this.record.update({ call_summary_html: html });
    }

    // ── Plumbing ─────────────────────────────────────────────

    async _jsonRpc(url, params) {
        const resp = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jsonrpc: "2.0", method: "call", params }),
        });
        if (!resp.ok) throw new Error(`RPC failed: ${resp.status}`);
        const data = await resp.json();
        if (data.error) {
            throw new Error(data.error.data?.message || data.error.message);
        }
        return data.result;
    }

    _teardown() {
        if (this._greetingAudio) {
            try {
                this._greetingAudio.pause();
            } catch (_err) {
                // ignore
            }
            this._greetingAudio.src = "";
            this._greetingAudio = null;
        }
        if (this._ws) {
            try {
                this._ws.close();
            } catch (_err) {
                // ignore
            }
            this._ws = null;
        }
        if (this._micNode) {
            try {
                this._micNode.disconnect();
            } catch (_err) {
                // ignore
            }
            this._micNode = null;
        }
        if (this._micStream) {
            this._micStream.getTracks().forEach((t) => t.stop());
            this._micStream = null;
        }
        if (this._micCtx) {
            this._micCtx.close().catch(() => {});
            this._micCtx = null;
        }
        if (this._playCtx) {
            this._playCtx.close().catch(() => {});
            this._playCtx = null;
            this._playQueueEnd = 0;
        }
    }
}

// ── Bubble + summary builders (module-level, no state) ───────

const BUBBLE_CALLER =
    "background-color:#dcf8c6;border-radius:12px;padding:8px 12px;" +
    "margin:6px 0 6px 80px;text-align:right;";
const BUBBLE_AGENT =
    "background-color:#f1f0f0;border-radius:12px;padding:8px 12px;" +
    "margin:6px 80px 6px 0;text-align:left;";
const BUBBLE_SYSTEM =
    "background-color:#fff3cd;border-radius:12px;padding:6px 12px;" +
    "margin:6px 40px;text-align:center;color:#663;font-size:12px;";
const WRAPPER_OPEN = '<div style="overflow:hidden;min-height:60px;">';
const WRAPPER_CLOSE = "</div>";

function escapeHtml(text) {
    return String(text || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function _buildBubble(role, text, meta) {
    const safe = escapeHtml(text);
    if (role === "caller") {
        return `<div style="${BUBBLE_CALLER}"><strong>Caller:</strong> ${safe}</div>`;
    }
    if (role === "system") {
        return `<div style="${BUBBLE_SYSTEM}">${safe}</div>`;
    }
    let html = `<div style="${BUBBLE_AGENT}"><strong>Agent:</strong> ${safe}`;
    if (meta && meta.followUp) {
        html += `<br/><em style="color:#555;">${escapeHtml(meta.followUp)}</em>`;
    }
    html += "</div>";
    return html;
}

function _appendToWrapper(existingHtml, bubbleHtml) {
    let inner = existingHtml;
    if (!inner) {
        inner = WRAPPER_OPEN + WRAPPER_CLOSE;
    }
    if (inner.endsWith(WRAPPER_CLOSE)) {
        return inner.slice(0, -WRAPPER_CLOSE.length) + bubbleHtml + WRAPPER_CLOSE;
    }
    return WRAPPER_OPEN + inner + bubbleHtml + WRAPPER_CLOSE;
}

const BADGE =
    "display:inline-block;padding:2px 8px;border-radius:10px;" +
    "font-size:11px;margin-right:6px;margin-top:4px;" +
    "background-color:#e3f0ff;color:#234;";
const PANEL =
    "border:1px solid #cde;border-radius:8px;padding:12px;" +
    "margin:8px 0;background-color:#f5f9ff;";

function _buildSummaryHtml(data) {
    const duration = data.duration_seconds || 0;
    const transcript = data.transcript || [];
    const avg = data.avg_confidence;
    const totalQueries = data.total_queries || 0;
    const summary = data.conversation_summary || "";
    const crm = data.crm_analysis;

    const badges = [
        `<span style="${BADGE}">Duration: ${duration}s</span>`,
        `<span style="${BADGE}">Turns: ${transcript.length}</span>`,
        `<span style="${BADGE}">Queries: ${totalQueries}</span>`,
    ];
    if (typeof avg === "number") {
        badges.push(
            `<span style="${BADGE}">Avg confidence: ${Math.round(avg * 100)}%</span>`
        );
    }
    if (crm) {
        if (crm.customer_intent)
            badges.push(`<span style="${BADGE}">Intent: ${escapeHtml(crm.customer_intent)}</span>`);
        if (crm.priority)
            badges.push(`<span style="${BADGE}">Priority: ${escapeHtml(crm.priority)}</span>`);
        if (crm.sentiment)
            badges.push(`<span style="${BADGE}">Sentiment: ${escapeHtml(crm.sentiment)}</span>`);
    }

    let html = `<div style="${PANEL}">`;
    html += `<div style="font-weight:600;color:#334;">Call Summary</div>`;
    html += `<div>${badges.join("")}</div>`;
    if (summary) {
        html += `<div style="margin-top:8px;"><strong>Summary:</strong> ${escapeHtml(summary)}</div>`;
    }
    html += "</div>";
    return html;
}

export const liveCallField = {
    component: LiveCallField,
    displayName: _t("Live Call"),
    supportedTypes: ["char"],
};

registry.category("fields").add("live_call", liveCallField);
