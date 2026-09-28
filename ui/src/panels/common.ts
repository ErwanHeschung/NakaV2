/**
 * What goes inside the drawer: one module per section, and the small pieces
 * they all share, here.
 *
 * Each renderer owns one section and re-renders its whole body from server
 * state. Edits are held in a local map until saved rather than read back off
 * the inputs, so what gets sent is always what the user typed and never
 * whatever the DOM happens to hold.
 */
import { ApiError } from '../api.js';
import { el, replace } from '../dom.js';
import { icon, type IconName } from '../ui.js';

/** Turns an ApiError's JSON body into the sentence the server put in it. */
export function reason(error: unknown): string {
  if (error instanceof ApiError) {
    try {
      const body: unknown = JSON.parse(error.message);
      if (typeof body === 'object' && body !== null && 'detail' in body) {
        return String(body.detail);
      }
    } catch {
      // Not JSON — fall through and show it as it came.
    }
    return error.message;
  }
  return String(error);
}

export function failed(body: HTMLElement, error: unknown): void {
  replace(body, el('p', { class: 'error' }, reason(error)));
}

/**
 * A placeholder while a section's data is fetched — only when opening it.
 *
 * On a refresh the old content stays until the new arrives. Blanking it
 * first collapsed the body to one line, which threw the scroll position
 * away: every toggle and every save landed the reader back at the top.
 */
export function loading(body: HTMLElement): void {
  if (body.dataset.refreshing !== undefined) return;
  replace(body, el('p', { class: 'muted' }, 'Loading…'));
}

/** Rebuild a section's body without losing the reader's place in it. */
export function rerender<T>(body: HTMLElement, render: (body: HTMLElement) => T): T {
  // Renderers call loading() synchronously as they start, which is the only
  // moment this flag is read, so it can come straight back off.
  body.dataset.refreshing = '';
  try {
    return render(body);
  } finally {
    delete body.dataset.refreshing;
  }
}

/**
 * Mark a section as holding unsaved input.
 *
 * A refresh rebuilds a section from scratch, which is right for a list and
 * destructive for a half-written note or a form with pending edits. The shell
 * checks this before rebuilding, so a change elsewhere never costs someone
 * what they were in the middle of typing.
 */
export function busy(body: HTMLElement, editing: boolean): void {
  if (editing) body.dataset.busy = 'yes';
  else delete body.dataset.busy;
}

/** A one-line result that clears itself, for saves and deletes. */
export function flash(host: HTMLElement, message: string, kind = 'muted'): void {
  replace(host, el('span', { class: kind }, message));
  setTimeout(() => {
    if (host.textContent === message) replace(host);
  }, 4000);
}

/** Services with a logo of their own, vendored by scripts/vendor-brands.mjs. */
const BRAND_LOGOS = new Set(['telegram', 'calendar', 'spotify']);

/** The service's own logo, or for one without (the weather), a drawn one. */
export function connectionLogo(name: string): HTMLElement {
  if (BRAND_LOGOS.has(name)) {
    return el(
      'span',
      { class: 'conn-logo' },
      el('img', { src: `./vendor/brands/${name}.svg`, alt: '' }),
    );
  }
  const drawn: IconName =
    name === 'weather' ? 'cloud-sun' : name === 'mcp' ? 'blocks' : 'plug';
  return el('span', { class: `conn-logo ${name}` }, icon(drawn));
}
