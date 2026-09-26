/** The Timers drawer: running timers, and reminders. */
import { api, type RemindersState, type Timer, type TimersState } from '../api.js';
import { el, replace } from '../dom.js';
import { ensureNotifications } from '../sound.js';
import { button, control, icon, numberField, textField } from '../ui.js';
import { reason, flash } from './common.js';

export function renderTimers(body: HTMLElement): () => void {
  // The list is polled and the form is not. Re-rendering the whole panel on a
  // poll would wipe whatever was half-typed into it every two seconds, and a
  // timer set by voice has to appear here without the panel being reopened —
  // which is what a render-once panel could never do.
  const listHost = el('div', {});
  const warning = el('div', {});
  const status = el('span', {});
  let label = '';
  let minutes = 5;

  /** Server time minus ours, so a clock skew is not read as time remaining. */
  let offset = 0;
  let showing: Timer[] = [];

  const paintRemaining = (): void => {
    for (const [index, node] of [...listHost.querySelectorAll('.left')].entries()) {
      const timer = showing[index];
      if (!timer) continue;
      const remaining = Math.round(timer.due - (Date.now() / 1000 + offset));
      node.textContent = remaining > 0 ? countdown(remaining) : 'ringing';
    }
  };

  const paintList = (state: TimersState): void => {
    offset = state.now - Date.now() / 1000;
    showing = state.timers;

    replace(
      warning,
      state.can_fire
        ? null
        : el(
            'div',
            { class: 'warning' },
            icon('triangle-alert', 'sm'),
            el('span', {}, state.note),
          ),
    );

    if (state.timers.length === 0) {
      replace(listHost, el('p', { class: 'muted' }, 'No timers running.'));
      return;
    }
    replace(
      listHost,
      el(
        'div',
        { class: 'list' },
        ...state.timers.map((timer) =>
          el(
            'div',
            { class: 'item row spaced' },
            el(
              'div',
              { class: 'row' },
              icon('clock', 'sm'),
              el('strong', {}, timer.label),
            ),
            el(
              'div',
              { class: 'row' },
              el('span', { class: 'value mono left' }, ''),
              button(
                'x',
                'Cancel',
                () => {
                  void api
                    .cancelTimer(timer.label)
                    .then(paintList)
                    .catch((error: unknown) => {
                      flash(status, reason(error), 'error');
                    });
                },
                'danger',
              ),
            ),
          ),
        ),
      ),
    );
    paintRemaining();
  };

  const reminderHost = el('div', {});
  const reminderStatus = el('span', {});
  let reminderText = '';
  let reminderDay = '';
  let reminderAt = '';

  const paintReminders = (state: RemindersState): void => {
    if (state.reminders.length === 0) {
      replace(reminderHost, el('p', { class: 'muted' }, 'No reminders set.'));
      return;
    }
    replace(
      reminderHost,
      el(
        'div',
        { class: 'list' },
        ...state.reminders.map((reminder) =>
          el(
            'div',
            { class: 'item row spaced' },
            el(
              'div',
              { class: 'row' },
              icon('bell-ring', 'sm'),
              el('strong', {}, reminder.text),
            ),
            el(
              'div',
              { class: 'row' },
              el('span', { class: 'value mono' }, reminder.when),
              button(
                'x',
                'Cancel',
                () => {
                  void api
                    .cancelReminder(reminder.id)
                    .then(paintReminders)
                    .catch((error: unknown) => {
                      flash(reminderStatus, reason(error), 'error');
                    });
                },
                'danger',
              ),
            ),
          ),
        ),
      ),
    );
  };

  const load = (): void => {
    void api
      .timers()
      .then(paintList)
      .catch((error: unknown) => {
        flash(status, reason(error), 'error');
      });
    void api
      .reminders()
      .then(paintReminders)
      .catch((error: unknown) => {
        flash(reminderStatus, reason(error), 'error');
      });
  };

  const dayInput = el('input', { class: 'input', type: 'date' });
  dayInput.addEventListener('input', () => {
    reminderDay = dayInput.value;
  });
  const atInput = el('input', { class: 'input', type: 'time' });
  atInput.addEventListener('input', () => {
    reminderAt = atInput.value;
  });

  replace(
    body,
    warning,
    listHost,
    el('h3', {}, 'New timer'),
    control(
      { label: 'For' },
      textField(
        '',
        (value) => {
          label = value;
        },
        'pasta',
      ),
    ),
    control(
      { label: 'Minutes' },
      numberField(
        minutes,
        (value) => {
          minutes = value;
        },
        { min: 0.1, max: 1440, step: 0.5 },
      ),
    ),
    el(
      'div',
      { class: 'row spaced' },
      button('circle-plus', 'Start timer', () => {
        if (!label.trim()) {
          flash(status, 'A timer needs a name.', 'error');
          return;
        }
        void ensureNotifications();
        void api
          .startTimer(label.trim(), Math.max(1, Math.round(minutes * 60)))
          .then(paintList)
          .catch((error: unknown) => {
            flash(status, reason(error), 'error');
          });
      }),
      status,
    ),
    el('h3', {}, 'Reminders'),
    el(
      'p',
      { class: 'muted' },
      'For a time of day. Kept across restarts, and sent to Telegram when it is connected.',
    ),
    reminderHost,
    control(
      { label: 'Remind me to' },
      textField(
        '',
        (value) => {
          reminderText = value;
        },
        'call Paul',
      ),
    ),
    el(
      'div',
      { class: 'row' },
      control({ label: 'Day', help: 'Empty: the next time it comes round.' }, dayInput),
      control({ label: 'At' }, atInput),
    ),
    el(
      'div',
      { class: 'row spaced' },
      button('bell-ring', 'Set reminder', () => {
        if (!reminderText.trim() || !reminderAt) {
          flash(reminderStatus, 'A reminder needs words and a time.', 'error');
          return;
        }
        void ensureNotifications();
        void api
          .addReminder(reminderText.trim(), reminderDay, reminderAt)
          .then(paintReminders)
          .catch((error: unknown) => {
            flash(reminderStatus, reason(error), 'error');
          });
      }),
      reminderStatus,
    ),
  );

  replace(listHost, el('p', { class: 'muted' }, 'Loading…'));
  load();
  // The only thing left on a timer here is the countdown, which changes once
  // a second on its own. Adding, cancelling and ringing all arrive as events
  // and re-render this section from the top, so there is nothing to poll.
  const ticking = setInterval(paintRemaining, 1000);
  return () => {
    clearInterval(ticking);
  };
}

const pad = (n: number): string => String(n).padStart(2, '0');

function countdown(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(rest)}`
    : `${minutes}:${pad(rest)}`;
}
