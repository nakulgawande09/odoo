/** @odoo-module */
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { Component, onWillUpdateProps } from "@odoo/owl";

/**
 * Invisible field widget that auto-plays TTS audio whenever
 * the field value changes. Attach to the `last_response_text` field
 * on the Test Console to get automatic voice responses.
 */
export class AutoTTSField extends Component {
    static template = "kb_connector.AutoTTSField";
    static props = { ...standardFieldProps };

    setup() {
        this._audio = null;
        this._lastPlayed = "";

        onWillUpdateProps((nextProps) => {
            // Voice mode owns its own TTS playback in LiveCallField — playing
            // here as well would double up the audio and trigger extra render
            // churn on every turn.
            if (nextProps.record.data.mode === "voice") return;
            const newVal = nextProps.record.data[nextProps.name] || "";
            if (newVal && newVal !== this._lastPlayed) {
                this._playTTS(newVal, nextProps.record);
            }
        });
    }

    _playTTS(text, record) {
        // Stop any currently playing audio
        if (this._audio) {
            this._audio.pause();
            this._audio = null;
        }

        this._lastPlayed = text;

        // Get agent_id from the record
        const agentId = record.data.agent_id && record.data.agent_id[0];
        if (!agentId) return;

        const encodedText = encodeURIComponent(text.substring(0, 500));
        const url = `/kb/tts/speak/${agentId}?text=${encodedText}`;

        this._audio = new Audio(url);
        this._audio.play().catch((err) => {
            console.warn("Auto-TTS playback failed:", err.message);
        });
    }
}

export const autoTTSField = {
    component: AutoTTSField,
    displayName: _t("Auto TTS"),
    supportedTypes: ["text", "char"],
};

registry.category("fields").add("auto_tts", autoTTSField);
