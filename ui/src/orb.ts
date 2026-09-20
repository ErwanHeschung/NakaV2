/**
 * The orb: the one thing on screen that tells you what she is doing.
 *
 * Grey and nearly still when idle, coloured and moving when she is working,
 * per the sketch. It is built to be driven by audio later — `setLevel` takes
 * a 0..1 amplitude — but until the panel has an audio source it runs on a
 * synthetic idle breath so the state is still legible.
 */

export type OrbState = 'sleeping' | 'idle' | 'thinking' | 'speaking';

interface Palette {
  readonly inner: string;
  readonly outer: string;
  readonly glow: number;
}

const PALETTES: Record<OrbState, Palette> = {
  // Grey when nothing is loaded: the sketch is explicit that colour means
  // active, so a sleeping assistant must not look like a working one.
  sleeping: { inner: '#3a4050', outer: '#232838', glow: 0.1 },
  idle: { inner: '#5b6478', outer: '#2c3346', glow: 0.22 },
  thinking: { inner: '#7c5cff', outer: '#3a2d7a', glow: 0.7 },
  speaking: { inner: '#4d9fff', outer: '#1d4a80', glow: 0.85 },
};

const RINGS = 3;

export class Orb {
  private readonly ctx: CanvasRenderingContext2D;
  private state: OrbState = 'sleeping';
  private level = 0;
  private smoothed = 0;
  private phase = 0;
  private frame = 0;

  constructor(private readonly canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('canvas 2d context unavailable');
    this.ctx = ctx;
    this.resize();
    globalThis.addEventListener('resize', () => {
      this.resize();
    });
    this.frame = requestAnimationFrame(this.tick);
  }

  setState(state: OrbState): void {
    this.state = state;
  }

  /** Amplitude 0..1. Wire this to an analyser node when audio is available. */
  setLevel(level: number): void {
    this.level = Math.max(0, Math.min(1, level));
  }

  destroy(): void {
    cancelAnimationFrame(this.frame);
  }

  private resize(): void {
    const dpr = globalThis.devicePixelRatio || 1;
    const rect = this.canvas.getBoundingClientRect();
    this.canvas.width = Math.round(rect.width * dpr);
    this.canvas.height = Math.round(rect.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  private readonly tick = (): void => {
    this.frame = requestAnimationFrame(this.tick);
    this.draw();
  };

  private draw(): void {
    const { ctx, canvas } = this;
    const dpr = globalThis.devicePixelRatio || 1;
    const w = canvas.width / dpr;
    const h = canvas.height / dpr;
    const cx = w / 2;
    const cy = h / 2;
    const base = Math.min(w, h) * 0.3;

    // A slow breath stands in for audio, so an idle orb still looks alive
    // rather than broken. Real levels override it once they exist.
    const breathing = this.state === 'thinking' ? 0.35 : 0.12;
    const synthetic = (Math.sin(this.phase) * 0.5 + 0.5) * breathing;
    const target = Math.max(this.level, synthetic);
    this.smoothed += (target - this.smoothed) * 0.12;
    this.phase += this.state === 'thinking' ? 0.055 : 0.018;

    const palette = PALETTES[this.state];
    ctx.clearRect(0, 0, w, h);

    // Outer glow.
    const glow = ctx.createRadialGradient(cx, cy, base * 0.5, cx, cy, base * 2.1);
    glow.addColorStop(0, hexToRgba(palette.inner, 0.28 * palette.glow));
    glow.addColorStop(1, hexToRgba(palette.inner, 0));
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, w, h);

    // Concentric rings, each lagging the one inside it so the motion reads as
    // a single body rather than separate circles.
    for (let i = RINGS - 1; i >= 0; i--) {
      const lag = i * 0.55;
      const wobble = Math.sin(this.phase - lag) * this.smoothed;
      const radius = base * (1 + i * 0.16) * (1 + wobble * 0.14);
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.strokeStyle = hexToRgba(
        palette.inner,
        (0.34 - i * 0.09) * (0.4 + palette.glow),
      );
      ctx.lineWidth = i === 0 ? 1.6 : 1;
      ctx.stroke();
    }

    // The body.
    const body = ctx.createRadialGradient(
      cx - base * 0.25,
      cy - base * 0.3,
      base * 0.1,
      cx,
      cy,
      base,
    );
    body.addColorStop(0, hexToRgba(palette.inner, 0.9));
    body.addColorStop(1, hexToRgba(palette.outer, 0.92));
    ctx.beginPath();
    ctx.arc(cx, cy, base * (1 + this.smoothed * 0.05), 0, Math.PI * 2);
    ctx.fillStyle = body;
    ctx.fill();

    // Rim light, brightest where the gradient's highlight sits.
    ctx.beginPath();
    ctx.arc(cx, cy, base * (1 + this.smoothed * 0.05), 0, Math.PI * 2);
    ctx.strokeStyle = hexToRgba(palette.inner, 0.5);
    ctx.lineWidth = 1;
    ctx.stroke();
  }
}

function hexToRgba(hex: string, alpha: number): string {
  const value = hex.replace('#', '');
  const r = Number.parseInt(value.slice(0, 2), 16);
  const g = Number.parseInt(value.slice(2, 4), 16);
  const b = Number.parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
}
