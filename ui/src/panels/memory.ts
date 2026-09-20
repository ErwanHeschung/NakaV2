/** What she remembers: permanent facts, the rolling summary, recent turns. */

import { api, type MemoryState } from '../api.js';
import { el, replace } from '../dom.js';

function render(host: HTMLElement, state: MemoryState): void {
  replace(
    host,
    el('h3', {}, `Facts (${state.facts.length})`),
    el('p', { class: 'muted' }, 'Persist across restarts. Maintained after each turn.'),
    el('ol', { class: 'facts' }, ...state.facts.map((fact) => el('li', {}, fact))),

    el('h3', {}, 'Summary'),
    el(
      'p',
      { class: state.summary ? '' : 'muted' },
      state.summary || 'Nothing folded in yet.',
    ),
    state.pending_summary > 0
      ? el(
          'p',
          { class: 'muted' },
          `${state.pending_summary} turn(s) waiting to be summarised`,
        )
      : null,

    el('h3', {}, `Recent turns (${state.recent.length})`),
    ...state.recent.map((turn) =>
      el(
        'div',
        { class: 'turn' },
        el('div', { class: 'said you' }, turn.user),
        el('div', { class: 'said naka' }, turn.naka),
      ),
    ),

    el(
      'div',
      { class: 'row' },
      el(
        'button',
        {
          onclick: () => {
            void refresh(host);
          },
        },
        'Refresh',
      ),
      el(
        'button',
        {
          onclick: () => {
            void api.reloadMemory().then(() => refresh(host));
          },
        },
        'Reload persona & facts from disk',
      ),
      el(
        'button',
        {
          class: 'danger',
          // Only the in-process conversation goes; the facts on disk stay.
          onclick: () => {
            if (!confirm('Clear the current conversation? Facts are kept.')) return;
            void api.forgetConversation().then(() => refresh(host));
          },
        },
        'Clear conversation',
      ),
    ),
  );
}

async function refresh(host: HTMLElement): Promise<void> {
  try {
    render(host, await api.memory());
  } catch (error) {
    replace(host, el('p', { class: 'error' }, String(error)));
  }
}

export function mountMemory(host: HTMLElement): void {
  void refresh(host);
}
