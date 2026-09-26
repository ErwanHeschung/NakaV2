/** The Memory drawer: remembered facts, the summary, clearing. */
import { api, type FactsState, type MemoryState } from '../api.js';
import { el, replace } from '../dom.js';
import { ask, button, iconButton, textField } from '../ui.js';
import { reason, failed, loading, busy, flash } from './common.js';

export function renderMemory(body: HTMLElement): void {
  loading(body);
  void Promise.all([api.memory(), api.facts()])
    .then(([state, facts]) => {
      paintMemory(body, state, facts);
    })
    .catch((error: unknown) => {
      failed(body, error);
    });
}

function paintMemory(body: HTMLElement, state: MemoryState, facts: FactsState): void {
  busy(body, false);
  const status = el('span', {});
  let draft = '';

  /** Re-read from the server after any change, so numbering stays truthful. */
  const again = (): void => {
    renderMemory(body);
  };

  const failedWith = (error: unknown): void => {
    flash(status, reason(error), 'error');
  };

  const rows = facts.facts.map((fact, index) => {
    const number = index + 1;
    // The text as the server last gave it. Sent back with any change so an
    // edit aimed at this line cannot land on a different one.
    const original = fact;
    let text = fact;

    const field = textField(fact, (value) => {
      text = value;
      // Editing anything counts as unsaved input: the memory panel
      // re-renders on every turn she takes, and that must not wipe a
      // half-typed correction.
      busy(body, value !== original);
    });

    return el(
      'div',
      { class: 'fact' },
      el('span', { class: 'fact-number' }, String(number)),
      field,
      iconButton('check', `Save fact ${number}`, () => {
        if (text.trim() === original) {
          flash(status, 'Nothing changed.');
          return;
        }
        void api.editFact(number, text, original).then(again).catch(failedWith);
      }),
      iconButton('trash', `Forget fact ${number}`, () => {
        void ask({
          title: 'Forget this fact?',
          body: fact,
          confirm: 'Forget',
          danger: true,
        }).then((yes) => {
          if (yes) void api.deleteFact(number, original).then(again).catch(failedWith);
        });
      }),
    );
  });

  const adder = textField(
    '',
    (value) => {
      draft = value;
      busy(body, value.trim() !== '');
    },
    'Something worth knowing months from now',
  );

  const full = facts.facts.length >= facts.max;

  replace(
    body,
    el(
      'div',
      { class: 'row spaced' },
      el('h3', {}, `Facts — ${facts.facts.length} of ${facts.max}`),
      status,
    ),
    facts.facts.length > 0
      ? el('div', { class: 'facts-list' }, ...rows)
      : el('p', { class: 'muted' }, 'Nothing remembered yet.'),
    el('h3', {}, 'Remember something'),
    adder,
    el(
      'div',
      { class: 'row spaced' },
      button('plus', full ? `Full at ${facts.max}` : 'Remember', () => {
        if (!draft.trim()) {
          flash(status, 'Nothing to remember.', 'error');
          return;
        }
        void api.addFact(draft).then(again).catch(failedWith);
      }),
      el('span', { class: 'muted' }, `up to ${facts.max_chars} characters each`),
    ),
    el('h3', {}, 'Summary'),
    el('p', { class: 'muted' }, state.summary || 'Nothing folded in yet.'),
    el(
      'div',
      { class: 'row spaced' },
      button('refresh-cw', 'Refresh', again),
      button(
        'trash',
        'Clear conversation',
        () => {
          void ask({
            title: 'Clear the conversation?',
            body: 'She forgets what was said and the summary. Remembered facts are kept.',
            confirm: 'Clear',
            danger: true,
          }).then((yes) => {
            if (yes) void api.forgetConversation().then(again).catch(failedWith);
          });
        },
        'danger',
      ),
    ),
  );
}
