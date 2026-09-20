/**
 * Push to talk, in the browser.
 *
 * Hold the configured key, speak, release. The panel then is the client: no
 * separate process has to be running for the thing on screen to listen.
 *
 * Two format choices are forced by what browsers can actually do, and both
 * are handled by the server rather than worked around here:
 *
 *   Recording — MediaRecorder produces WebM/Opus, never WAV. Capturing raw
 *   PCM instead would mean an AudioWorklet and a hand-written WAV header, so
 *   the server decodes what the browser natively produces.
 *
 *   Playback — no browser streams Ogg through MediaSource, so asking for Ogg
 *   back would mean holding the whole reply before the first word. The reply
 *   is requested as raw PCM and the chunks are scheduled as they land, which
 *   keeps the sentence-by-sentence streaming the server already does.
 */

import { type ClientConfig, api } from './api.js';
import { audioContext } from './sound.js';

export type TalkState = 'off' | 'ready' | 'listening' | 'thinking' | 'speaking';

interface Handlers {
  onState: (state: TalkState, detail?: string) => void;
  /** 0..1 microphone amplitude, for the orb. */
  onLevel: (level: number) => void;
  onTranscript?: () => void;
}

/** Below this an utterance is a key bounce, not speech. */
const MIN_SECONDS = 0.3;

function same(a: ClientConfig, b: ClientConfig): boolean {
  return (
    a.push_to_talk_key === b.push_to_talk_key &&
    a.listen_when_open === b.listen_when_open &&
    a.agentic === b.agentic &&
    a.sample_rate === b.sample_rate
  );
}

export class Talk {
  private config: ClientConfig | null = null;
  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private analyser: AnalyserNode | null = null;
  private levelFrame = 0;
  private held = false;
  private startedAt = 0;
  private state: TalkState = 'off';
  /** Where the next reply chunk is scheduled. Ahead of `currentTime`. */
  private playHead = 0;

  constructor(private readonly handlers: Handlers) {}

  /** Read the config, bind the key, and open the mic if that is the setting. */
  async start(): Promise<void> {
    this.config = await api.clientConfig();
    globalThis.addEventListener('keydown', this.onKeyDown);
    globalThis.addEventListener('keyup', this.onKeyUp);
    // A key released outside the window never reaches keyup, which would
    // otherwise leave it recording forever.
    globalThis.addEventListener('blur', this.onKeyUp);
    if (this.config.listen_when_open) await this.arm();
    else this.set('off', 'microphone not open');
  }

  /**
   * Re-read the config and apply anything that changed.
   *
   * Called on a save and on the slow poll, because a tab left open otherwise
   * keeps whatever it read at load time forever — which is how a page open
   * across a settings change went on asking for no tools long after the
   * server had started offering them.
   */
  async refresh(): Promise<void> {
    const next = await api.clientConfig();
    const before = this.config;
    this.config = next;
    if (before && same(before, next)) return;

    if (next.listen_when_open && !this.armed) await this.arm();
    else if (!next.listen_when_open && this.armed) this.disarm();
    else if (this.armed && this.state === 'ready') this.set('ready');
  }

  get key(): string {
    return this.config?.push_to_talk_key ?? '';
  }

  get armed(): boolean {
    return this.stream !== null;
  }

  /** Ask for the microphone and hold it. Safe to call when already armed. */
  async arm(): Promise<void> {
    if (this.stream) return;
    if (!globalThis.isSecureContext) {
      // http://127.0.0.1 is a secure context; a LAN address is not, and the
      // failure is otherwise a bare "undefined is not an object".
      this.set('off', 'microphone needs 127.0.0.1 or https');
      return;
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch (error) {
      this.set('off', error instanceof Error ? error.message : 'no microphone');
      return;
    }
    const audio = this.context();
    this.analyser = audio.createAnalyser();
    this.analyser.fftSize = 512;
    audio.createMediaStreamSource(this.stream).connect(this.analyser);
    this.set('ready');
  }

  /** Release the microphone, which also puts out the browser's recording dot. */
  disarm(): void {
    this.stopLevels();
    if (this.recorder?.state === 'recording') this.recorder.stop();
    this.recorder = null;
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    this.stream = null;
    this.analyser = null;
    this.held = false;
    this.set('off', 'microphone released');
  }

  async toggle(): Promise<void> {
    if (this.armed) this.disarm();
    else await this.arm();
  }

  private context(): AudioContext {
    return audioContext();
  }

  private set(state: TalkState, detail?: string): void {
    this.state = state;
    this.handlers.onState(state, detail);
  }

  private readonly onKeyDown = (event: KeyboardEvent): void => {
    if (!this.accepts(event) || event.repeat || this.held) return;
    event.preventDefault();
    this.held = true;
    this.beginRecording();
  };

  private readonly onKeyUp = (event: Event): void => {
    if (event instanceof KeyboardEvent && !this.accepts(event)) return;
    if (!this.held) return;
    this.held = false;
    this.endRecording();
  };

  /**
   * Whether this keypress is the talk key and not someone typing.
   *
   * The key is configurable down to a letter, so a note being edited would
   * otherwise start recording on every occurrence of it.
   */
  private accepts(event: KeyboardEvent): boolean {
    if (event.code !== this.key || !this.stream) return false;
    const target = event.target;
    if (target instanceof HTMLElement) {
      const tag = target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable) {
        return false;
      }
    }
    return true;
  }

  private beginRecording(): void {
    if (!this.stream || this.state === 'listening') return;
    // Told the moment the key goes down, so an idle reload of the models
    // overlaps with speaking instead of following it.
    void api.wake().catch(() => null);
    // Resuming here rather than on load: an AudioContext created without a
    // gesture starts suspended, and this is the first gesture there is.
    void this.context().resume();

    this.chunks = [];
    this.recorder = new MediaRecorder(this.stream);
    this.recorder.addEventListener('dataavailable', (event: BlobEvent) => {
      if (event.data.size > 0) this.chunks.push(event.data);
    });
    this.recorder.start();
    this.startedAt = performance.now();
    this.set('listening');
    this.watchLevels();
  }

  private endRecording(): void {
    const recorder = this.recorder;
    this.stopLevels();
    if (!recorder || recorder.state !== 'recording') return;
    const seconds = (performance.now() - this.startedAt) / 1000;
    recorder.addEventListener(
      'stop',
      () => {
        if (seconds < MIN_SECONDS) {
          this.set('ready', 'too short');
          return;
        }
        void this.send(new Blob(this.chunks, { type: recorder.mimeType }));
      },
      { once: true },
    );
    recorder.stop();
  }

  private watchLevels(): void {
    const analyser = this.analyser;
    if (!analyser) return;
    const buffer = new Uint8Array(analyser.frequencyBinCount);
    const tick = (): void => {
      this.levelFrame = requestAnimationFrame(tick);
      analyser.getByteTimeDomainData(buffer);
      let sum = 0;
      for (const sample of buffer) {
        const centred = (sample - 128) / 128;
        sum += centred * centred;
      }
      // Speech sits low in a 0..1 RMS, so it is lifted to a range the orb can
      // actually show rather than reported faithfully and rendered as nothing.
      const rms = Math.sqrt(sum / buffer.length);
      this.handlers.onLevel(Math.min(1, rms * 4));
    };
    tick();
  }

  private stopLevels(): void {
    cancelAnimationFrame(this.levelFrame);
    this.handlers.onLevel(0);
  }

  private async send(recorded: Blob): Promise<void> {
    this.set('thinking');
    const body = new FormData();
    body.append('file', recorded, 'speech.webm');
    body.append('agentic', String(this.config?.agentic ?? false));
    body.append('format', 'pcm');

    let response: Response;
    try {
      response = await fetch('/converse', { method: 'POST', body });
    } catch (error) {
      this.set('ready', error instanceof Error ? error.message : 'send failed');
      return;
    }
    if (!response.ok || !response.body) {
      const detail = await response.text().catch(() => response.statusText);
      this.set('ready', detail.slice(0, 120) || 'no reply');
      return;
    }

    this.handlers.onTranscript?.();
    const rate =
      Number(response.headers.get('X-Sample-Rate')) ||
      (this.config?.sample_rate ?? 24000);
    await this.play(response.body, rate);
    this.set(this.armed ? 'ready' : 'off');
  }

  /**
   * Play mono 16-bit PCM as it arrives.
   *
   * Each chunk is scheduled at the end of the previous one rather than played
   * on arrival, so the sentences join without a gap even though they are
   * synthesised one at a time.
   */
  private async play(body: ReadableStream<Uint8Array>, rate: number): Promise<void> {
    const audio = this.context();
    await audio.resume();
    this.playHead = Math.max(this.playHead, audio.currentTime);
    const reader = body.getReader();
    // A chunk can split a sample down the middle; the odd byte waits here.
    let carry = new Uint8Array(0);
    let spoke = false;
    let last: AudioBufferSourceNode | null = null;

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      const merged = new Uint8Array(carry.length + value.length);
      merged.set(carry);
      merged.set(value, carry.length);
      const usable = merged.length - (merged.length % 2);
      carry = merged.subarray(usable);
      if (usable === 0) continue;

      const pcm = new Int16Array(merged.buffer.slice(0, usable));
      const buffer = audio.createBuffer(1, pcm.length, rate);
      const channel = buffer.getChannelData(0);
      for (let i = 0; i < pcm.length; i++) channel[i] = (pcm[i] ?? 0) / 32768;

      const source = audio.createBufferSource();
      source.buffer = buffer;
      source.connect(audio.destination);
      // Never behind the clock: a late start would overlap the previous chunk.
      this.playHead = Math.max(this.playHead, audio.currentTime);
      source.start(this.playHead);
      this.playHead += buffer.duration;
      last = source;

      if (!spoke) {
        spoke = true;
        this.set('speaking');
      }
    }

    if (last) {
      await new Promise<void>((resolve) => {
        last.addEventListener('ended', () => {
          resolve();
        });
      });
    }
  }
}
