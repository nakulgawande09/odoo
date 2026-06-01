/**
 * AudioWorklet that downsamples the browser's native sample rate (typically
 * 48 kHz) to the 16 kHz mono PCM frames Gemini Live expects.
 *
 * Posts Int16 PCM chunks to the main thread every ~80 ms (1280 samples
 * at 16 kHz) so the WebSocket sees a steady cadence without choking the
 * audio thread.
 *
 * Note: this file is registered as a static asset under
 *   /web/content/kb_connector/static/src/js/live_audio_worklet.js
 * and loaded with `audioContext.audioWorklet.addModule(thatUrl)`.
 */

const TARGET_SAMPLE_RATE = 16000;
const CHUNK_SAMPLES = 1280; // ~80 ms at 16 kHz

class LivePcmDownsampler extends AudioWorkletProcessor {
    constructor() {
        super();
        this._inputRate = sampleRate; // global from worklet scope
        this._ratio = this._inputRate / TARGET_SAMPLE_RATE;
        this._buffer = new Float32Array(CHUNK_SAMPLES);
        this._fill = 0;
        // Carry leftover sub-sample position across blocks so decimation
        // doesn't drift. Starts at 0; after each block we advance by
        // (samplesConsumed * ratio - samplesEmitted * ratio).
        this._position = 0;
    }

    process(inputs) {
        const input = inputs[0];
        if (!input || input.length === 0) {
            return true;
        }
        const channel = input[0];
        if (!channel || channel.length === 0) {
            return true;
        }

        // Decimate: pick samples at integer positions in the input,
        // advancing by `_ratio` each emitted sample.
        let pos = this._position;
        while (pos < channel.length) {
            const idx = Math.floor(pos);
            this._buffer[this._fill++] = channel[idx];
            if (this._fill >= CHUNK_SAMPLES) {
                this._emitChunk();
                this._fill = 0;
            }
            pos += this._ratio;
        }
        this._position = pos - channel.length;
        return true;
    }

    _emitChunk() {
        const out = new Int16Array(CHUNK_SAMPLES);
        for (let i = 0; i < CHUNK_SAMPLES; i++) {
            const s = Math.max(-1, Math.min(1, this._buffer[i]));
            out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        // Transfer ownership of the buffer so the main thread
        // can post it straight to the WebSocket without copying.
        this.port.postMessage(out.buffer, [out.buffer]);
    }
}

registerProcessor("live-pcm-downsampler", LivePcmDownsampler);
