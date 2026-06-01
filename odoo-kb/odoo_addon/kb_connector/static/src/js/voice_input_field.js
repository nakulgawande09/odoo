/** @odoo-module */
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { CharField, charField } from "@web/views/fields/char/char_field";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { Component, useState } from "@odoo/owl";

/**
 * Voice input wrapper: renders a CharField with a microphone button
 * that uses the Web Speech API for speech-to-text input.
 */
export class VoiceInputField extends Component {
    static template = "kb_connector.VoiceInputField";
    static components = { CharField };
    static props = { ...standardFieldProps };

    setup() {
        const SR = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition;
        this.state = useState({
            listening: false,
            supported: !!SR,
        });
        this._recognition = null;
    }

    get fieldProps() {
        return this.props;
    }

    toggleMicrophone() {
        if (this.state.listening) {
            this._stopListening();
        } else {
            this._startListening();
        }
    }

    _startListening() {
        const SR = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition;
        if (!SR) return;

        this._recognition = new SR();
        this._recognition.lang = "en-US";
        this._recognition.interimResults = false;
        this._recognition.maxAlternatives = 1;

        this._recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript;
            this.props.record.update({ [this.props.name]: transcript });
        };

        this._recognition.onerror = (event) => {
            console.warn("Speech recognition error:", event.error);
            this.state.listening = false;
        };

        this._recognition.onend = () => {
            this.state.listening = false;
        };

        this.state.listening = true;
        this._recognition.start();
    }

    _stopListening() {
        if (this._recognition) {
            this._recognition.stop();
            this._recognition = null;
        }
        this.state.listening = false;
    }
}

export const voiceInputField = {
    ...charField,
    component: VoiceInputField,
    displayName: _t("Voice Input"),
    supportedTypes: ["char"],
};

registry.category("fields").add("voice_input", voiceInputField);
