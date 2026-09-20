/**
 * The little that replaces a framework here.
 *
 * Panels re-render wholesale from state rather than patching individual nodes.
 * At this size that is both simpler and fast enough — the expensive thing a
 * virtual DOM avoids is re-rendering large trees, and these trees are small.
 * The rule that keeps it honest: never read state out of the DOM, only write
 * to it.
 */

type Child = Node | string | number | null | undefined | false;

type Attrs = Record<
  string,
  string | number | boolean | EventListener | undefined | null
>;

/** Create an element. Keys starting with "on" become listeners. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Attrs = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'class') {
      node.className = String(value);
    } else {
      node.setAttribute(key, String(value));
    }
  }
  node.append(...flatten(children));
  return node;
}

function flatten(children: Child[]): (Node | string)[] {
  const out: (Node | string)[] = [];
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    out.push(typeof child === 'number' ? String(child) : child);
  }
  return out;
}

export function replace(host: HTMLElement, ...children: Child[]): void {
  host.replaceChildren(...flatten(children));
}

/**
 * Re-run `tick` on an interval, and once immediately.
 *
 * Failures are reported rather than silently ending the loop: an interval that
 * dies on one bad response leaves a panel frozen with stale data and no sign
 * that it has stopped updating.
 */
export function poll(
  everyMs: number,
  tick: () => Promise<void>,
  onError: (error: unknown) => void,
): () => void {
  let stopped = false;
  const run = async (): Promise<void> => {
    if (stopped) return;
    try {
      await tick();
    } catch (error) {
      onError(error);
    }
  };
  void run();
  const handle = setInterval(() => void run(), everyMs);
  return () => {
    stopped = true;
    clearInterval(handle);
  };
}

export function relativeTime(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}
