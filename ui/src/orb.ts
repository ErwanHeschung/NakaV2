/**
 * The orb: the one thing on screen that says what she is doing.
 *
 * Built the way the good voice orbs are built — several blobs whose outlines
 * are deformed by noise, each on its own phase and hue, layered with additive
 * blending and blurred so their edges melt together. The first version here
 * was concentric circles around a radial gradient, which renders as a dead
 * ball: a circle stays a circle, and nothing about it reads as alive.
 *
 * `setLevel` takes a 0..1 amplitude for when the panel has real audio; until
 * then a slow breath drives it, so idle still moves rather than freezing.
 */

export type OrbState = 'sleeping' | 'idle' | 'thinking' | 'speaking';

interface Layer {
  readonly hue: string;
  /** Radians per frame. Opposing signs stop the layers moving as one mass. */
  readonly spin: number;
  readonly scale: number;
  readonly wobble: number;
}

interface Palette {
  readonly layers: readonly Layer[];
  readonly core: string;
  readonly intensity: number;
}

// Grey and slow asleep, coloured and moving when working. The sketch is
// explicit that colour means active, so these stay far apart.
const PALETTES: Record<OrbState, Palette> = {
  sleeping: {
    layers: [
      { hue: '#59617a', spin: 0.0016, scale: 1, wobble: 0.085 },
      { hue: '#434b60', spin: -0.0011, scale: 0.92, wobble: 0.07 },
    ],
    core: '#aab3c8',
    intensity: 0.75,
  },
  idle: {
    layers: [
      { hue: '#6b78a0', spin: 0.0028, scale: 1, wobble: 0.12 },
      { hue: '#4a7fb5', spin: -0.0019, scale: 0.94, wobble: 0.11 },
      { hue: '#5b5a9e', spin: 0.0012, scale: 0.87, wobble: 0.1 },
    ],
    core: '#c8d2e8',
    intensity: 0.85,
  },
  thinking: {
    layers: [
      { hue: '#7c5cff', spin: 0.006, scale: 1, wobble: 0.17 },
      { hue: '#4d9fff', spin: -0.0045, scale: 0.94, wobble: 0.15 },
      { hue: '#b06cff', spin: 0.0032, scale: 0.87, wobble: 0.14 },
    ],
    core: '#cfc4ff',
    intensity: 0.95,
  },
  speaking: {
    layers: [
      { hue: '#4d9fff', spin: 0.005, scale: 1, wobble: 0.18 },
      { hue: '#3fcf8e', spin: -0.0038, scale: 0.94, wobble: 0.16 },
      { hue: '#7c5cff', spin: 0.0026, scale: 0.88, wobble: 0.15 },
    ],
    core: '#bfe2ff',
    intensity: 1.0,
  },
};

const POINTS = 96;

/**
 * Cheap looping value noise for the outline.
 *
 * A few sines at unrelated frequencies is enough: the eye reads "organic"
 * long before it reads "true gradient noise", and this costs nothing and
 * needs no dependency. Integer multiples of the angle keep the curve closed.
 */
function wave(angle: number, time: number, seed: number): number {
  return (
    Math.sin(angle * 2 + time * 1.1 + seed) * 0.5 +
    Math.sin(angle * 3 - time * 0.8 + seed * 1.7) * 0.3 +
    Math.sin(angle * 5 + time * 0.5 + seed * 2.3) * 0.2
  );
}

export class Orb {
  private readonly ctx: CanvasRenderingContext2D;
  private state: OrbState = 'sleeping';
  private level = 0;
  private smoothed = 0;
  private time = 0;
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

  /** Amplitude 0..1. Wire this to an analyser node when audio exists. */
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
    const base = Math.min(w, h) * 0.33;
    const palette = PALETTES[this.state];

    const breath = this.state === 'sleeping' ? 0.1 : 0.24;
    const synthetic = (Math.sin(this.time * 1.4) * 0.5 + 0.5) * breath;
    this.smoothed += (Math.max(this.level, synthetic) - this.smoothed) * 0.1;
    this.time += this.state === 'thinking' ? 0.016 : 0.007;

    ctx.clearRect(0, 0, w, h);

    // Halo first, so the blobs sit inside their own light.
    const first = palette.layers[0]?.hue ?? palette.core;
    const halo = ctx.createRadialGradient(cx, cy, base * 0.3, cx, cy, base * 1.9);
    halo.addColorStop(0, withAlpha(palette.core, 0.08 * palette.intensity));
    halo.addColorStop(0.5, withAlpha(first, 0.07 * palette.intensity));
    halo.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, w, h);

    // Additive, blurred layers. Where they overlap the colour builds, which
    // is what gives the middle its glow without drawing one.
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.filter = `blur(${(base * 0.045).toFixed(1)}px)`;

    palette.layers.forEach((layer, index) => {
      const radius = base * layer.scale;
      const amount = layer.wobble * (0.6 + this.smoothed * 1.7);
      const seed = index * 2.4;
      const spin = this.time * layer.spin * 260;

      // Sampled points joined with quadratic curves through their midpoints.
      // Straight segments between them left a visibly faceted silhouette at
      // this deformation amplitude, however many points were used.
      const points: { x: number; y: number }[] = [];
      for (let i = 0; i < POINTS; i++) {
        const angle = (i / POINTS) * Math.PI * 2;
        const r = radius * (1 + wave(angle + spin, this.time, seed) * amount);
        points.push({ x: cx + Math.cos(angle) * r, y: cy + Math.sin(angle) * r });
      }

      ctx.beginPath();
      const last = points[POINTS - 1]!;
      const start = points[0]!;
      ctx.moveTo((last.x + start.x) / 2, (last.y + start.y) / 2);
      for (let i = 0; i < POINTS; i++) {
        const current = points[i]!;
        const next = points[(i + 1) % POINTS]!;
        ctx.quadraticCurveTo(
          current.x,
          current.y,
          (current.x + next.x) / 2,
          (current.y + next.y) / 2,
        );
      }
      ctx.closePath();

      // The fill only shades the blob; it never fades it out. A radial
      // gradient is a circle and the path is not, so any falloff tied to a
      // radius caps the bulges flat and the silhouette reads as a polygon —
      // which is exactly what the wobbliest state showed. The blur softens
      // the edge instead, evenly, whatever shape the outline took.
      const reach = radius * (1 + amount);
      const fill = ctx.createRadialGradient(
        cx - radius * 0.3,
        cy - radius * 0.32,
        radius * 0.05,
        cx,
        cy,
        reach,
      );
      fill.addColorStop(0, withAlpha(layer.hue, 0.4 * palette.intensity));
      fill.addColorStop(1, withAlpha(layer.hue, 0.27 * palette.intensity));
      ctx.fillStyle = fill;
      ctx.fill();
    });
    ctx.restore();

    // A dim highlight, offset like a lit sphere. Kept weak and small: a
    // bright central core is what bleached the hue out of the middle.
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    const hx = cx - base * 0.22;
    const hy = cy - base * 0.26;
    const core = ctx.createRadialGradient(hx, hy, 0, hx, hy, base * 0.5);
    core.addColorStop(0, withAlpha(palette.core, 0.14 * palette.intensity));
    core.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(hx, hy, base * 0.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
}

/** Accepts #rgb, #rrggbb and #rrggbbaa; any baked alpha is multiplied in. */
function withAlpha(hex: string, alpha: number): string {
  const value = hex.replace('#', '');
  const wide = value.length <= 4;
  const part = (index: number): number => {
    const slice = wide
      ? (value[index] ?? '0').repeat(2)
      : value.slice(index * 2, index * 2 + 2);
    return Number.parseInt(slice, 16) || 0;
  };
  const baked = value.length === 8 || value.length === 4 ? part(3) / 255 : 1;
  const final = Math.max(0, Math.min(1, alpha * baked));
  return `rgba(${part(0)}, ${part(1)}, ${part(2)}, ${final.toFixed(3)})`;
}
