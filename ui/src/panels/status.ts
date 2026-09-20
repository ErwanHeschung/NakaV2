/** GPU and model state, plus the manual load/unload controls. */

import { api, type OpsStatus } from '../api.js';
import { el, poll, relativeTime, replace } from '../dom.js';

const VRAM_TOTAL_MB = 16303;

function bar(usedMb: number): HTMLElement {
  const percent = Math.min(100, (usedMb / VRAM_TOTAL_MB) * 100);
  return el(
    'div',
    { class: 'bar' },
    el('div', { class: 'bar-fill', style: `width:${percent.toFixed(1)}%` }),
  );
}

function render(host: HTMLElement, state: OpsStatus, busy: boolean): void {
  const awake = state.models_loaded && state.llm_up === true;
  replace(
    host,
    el(
      'div',
      { class: 'row' },
      el('span', { class: `dot ${awake ? 'on' : 'off'}` }),
      el('strong', {}, awake ? 'Awake' : 'Sleeping'),
      el(
        'span',
        { class: 'muted' },
        `idle ${relativeTime(state.idle_seconds)}`,
        state.in_flight > 0 ? ` · ${state.in_flight} in flight` : '',
      ),
    ),
    bar(state.vram_used_mb),
    el(
      'p',
      { class: 'muted' },
      `${(state.vram_used_mb / 1024).toFixed(1)} GB used, ` +
        `${(state.vram_free_mb / 1024).toFixed(1)} GB free`,
    ),
    el(
      'div',
      { class: 'row' },
      el(
        'button',
        {
          disabled: busy || !state.models_loaded,
          onclick: () => {
            void act(host, () => api.unload());
          },
        },
        'Release GPU',
      ),
      el(
        'button',
        {
          disabled: busy || awake,
          onclick: () => {
            void act(host, () => api.load());
          },
        },
        'Wake',
      ),
    ),
  );
}

/** Disable the controls while a transition runs; both take seconds. */
async function act(host: HTMLElement, action: () => Promise<unknown>): Promise<void> {
  const state = await api.opsStatus();
  render(host, state, true);
  try {
    await action();
  } finally {
    render(host, await api.opsStatus(), false);
  }
}

export function mountStatus(host: HTMLElement): () => void {
  return poll(
    2000,
    async () => {
      render(host, await api.opsStatus(), false);
    },
    (error) => {
      replace(host, el('p', { class: 'error' }, String(error)));
    },
  );
}
