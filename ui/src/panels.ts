/**
 * What goes inside the drawer.
 *
 * Each renderer owns one section and re-renders its whole body from server
 * state. Edits are held in a local map until saved rather than read back off
 * the inputs, so what gets sent is always what the user typed and never
 * whatever the DOM happens to hold.
 */

import {
  ApiError,
  api,
  type FactsState,
  type MemoryState,
  type ConnectionInfo,
  type ConnectionsState,
  type McpServer,
  type McpServerIn,
  type McpState,
  type NotesState,
  type RemindersState,
  type SettingField,
  type SettingsState,
  type Timer,
  type TimersState,
  type ToolInfo,
  type ToolsState,
} from './api.js';
import { el, relativeTime, replace } from './dom.js';
import { ensureNotifications } from './sound.js';
import {
  ask,
  button,
  choiceField,
  control,
  icon,
  iconButton,
  type IconName,
  keyField,
  numberField,
  textArea,
  textField,
  toggle,
} from './ui.js';

/** Turns an ApiError's JSON body into the sentence the server put in it. */
function reason(error: unknown): string {
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

function failed(body: HTMLElement, error: unknown): void {
  replace(body, el('p', { class: 'error' }, reason(error)));
}

/**
 * A placeholder while a section's data is fetched — only when opening it.
 *
 * On a refresh the old content stays until the new arrives. Blanking it
 * first collapsed the body to one line, which threw the scroll position
 * away: every toggle and every save landed the reader back at the top.
 */
function loading(body: HTMLElement): void {
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
function busy(body: HTMLElement, editing: boolean): void {
  if (editing) body.dataset.busy = 'yes';
  else delete body.dataset.busy;
}

/** A one-line result that clears itself, for saves and deletes. */
function flash(host: HTMLElement, message: string, kind = 'muted'): void {
  replace(host, el('span', { class: kind }, message));
  setTimeout(() => {
    if (host.textContent === message) replace(host);
  }, 4000);
}

/* ----------------------------------------------------------------- memory */

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

/* ------------------------------------------------------------------ tools */

const TOOL_GROUPS: {
  power: 'web' | 'shell' | null;
  title: string;
  off: string;
}[] = [
  { power: null, title: 'Everyday', off: '' },
  {
    power: 'web',
    title: 'Web',
    off: 'Off. She answers from what she already knows.',
  },
  {
    power: 'shell',
    title: 'PowerShell',
    off: 'Off. She cannot run anything on this PC.',
  },
];

const CONFIRM_TAG: Record<ToolInfo['confirms'], string | null> = {
  always: 'asks first',
  changes: 'asks before changes',
  never: null,
};

function toolItem(tool: ToolInfo): HTMLElement {
  const tag = CONFIRM_TAG[tool.confirms];
  return el(
    'div',
    { class: tool.enabled ? 'item' : 'item off', title: tool.name },
    el(
      'div',
      { class: 'row' },
      el('strong', {}, tool.label),
      tag === null ? null : el('span', { class: 'tag warn' }, tag),
    ),
    el('div', { class: 'muted' }, tool.summary),
  );
}

export function renderTools(body: HTMLElement): void {
  loading(body);
  void api
    .tools()
    .then((state: ToolsState) => {
      const offered = state.allowed.filter((t) => t.enabled).length;
      const waiting = state.awaiting_confirmation;
      const label = (name: string): string =>
        state.allowed.find((t) => t.name === name)?.label ?? name;

      const groups = TOOL_GROUPS.map(({ power, title, off }) => {
        const tools = state.allowed.filter((t) => t.power === power);
        if (tools.length === 0) return null;
        const on = power === null || state.powers[power];
        // The switch lives here as well as in Settings: this is where the
        // missing tools are noticed, so this is where they get turned on.
        const heading = power
          ? el(
              'div',
              { class: 'row spaced' },
              el('h3', {}, title),
              toggle(on, (value) => {
                void api
                  .saveSettings({ [`settings.powers.${power}`]: value })
                  .then(() => {
                    rerender(body, renderTools);
                  })
                  .catch((error: unknown) => {
                    failed(body, error);
                  });
              }),
            )
          : el('h3', {}, title);
        return el(
          'section',
          {},
          heading,
          on ? null : el('p', { class: 'muted' }, off),
          el('div', { class: 'list' }, ...tools.map((t) => toolItem(t))),
        );
      });

      // One group per connection, after the powers. Their switches live on
      // the Connections cards, where the setup that goes with them is.
      const linked = Object.entries(state.connections).map(([name, conn]) => {
        const tools = state.allowed.filter((t) => t.power === `conn.${name}`);
        if (tools.length === 0) return null;
        return el(
          'section',
          {},
          el(
            'div',
            { class: 'row spaced' },
            el('h3', {}, conn.label),
            el(
              'a',
              { class: 'muted', href: '#connections' },
              conn.ready ? 'connected' : conn.on ? 'not set up' : 'off',
            ),
          ),
          el('div', { class: 'list' }, ...tools.map((t) => toolItem(t))),
        );
      });

      // And one per running MCP server, named by the power its tools carry.
      const servers = [
        ...new Set(
          state.allowed
            .map((t) => t.power)
            .filter((p): p is `mcp.${string}` => p?.startsWith('mcp.') === true),
        ),
      ];
      const plugged = servers.map((power) =>
        el(
          'section',
          {},
          el(
            'div',
            { class: 'row spaced' },
            el('h3', {}, `MCP: ${power.slice(4)}`),
            el('a', { class: 'muted', href: '#connections' }, 'server'),
          ),
          el(
            'div',
            { class: 'list' },
            ...state.allowed.filter((t) => t.power === power).map((t) => toolItem(t)),
          ),
        ),
      );

      replace(
        body,
        el(
          'p',
          { class: 'muted' },
          `${offered} offered · up to ${state.max_steps} steps · ${state.timeout_s}s per request`,
        ),
        waiting
          ? el(
              'div',
              { class: 'warning' },
              icon('triangle-alert', 'sm'),
              el(
                'span',
                {},
                `Waiting for your yes: ${label(waiting.name)}` +
                  (typeof waiting.arguments.command === 'string'
                    ? ` — ${waiting.arguments.command}`
                    : ''),
              ),
            )
          : null,
        ...groups,
        ...linked,
        ...plugged,
      );
    })
    .catch((error: unknown) => {
      failed(body, error);
    });
}

/* ------------------------------------------------------------------ notes */

export function renderNotes(body: HTMLElement): void {
  busy(body, false);
  loading(body);
  void api
    .notes()
    .then((state: NotesState) => {
      replace(
        body,
        el(
          'div',
          { class: 'row spaced' },
          el('span', { class: 'muted mono' }, state.directory),
          button('plus', 'New', () => {
            openNote(body, null);
          }),
        ),
        state.notes.length > 0
          ? el(
              'div',
              { class: 'list' },
              ...state.notes.map((note) =>
                el(
                  'button',
                  {
                    class: 'item clickable',
                    onclick: () => {
                      openNote(body, note.name);
                    },
                  },
                  el(
                    'div',
                    { class: 'row' },
                    icon('file-text', 'sm'),
                    el('strong', {}, note.name),
                    el('div', { class: 'spacer' }),
                    el('span', { class: 'muted' }, modifiedAgo(note.modified)),
                  ),
                  el('div', { class: 'muted ellipsis' }, note.preview || 'Empty.'),
                ),
              ),
            )
          : el('p', { class: 'muted' }, 'No notes yet.'),
      );
    })
    .catch((error: unknown) => {
      failed(body, error);
    });
}

/** `null` opens a blank note; the name is asked for on the first save. */
function openNote(body: HTMLElement, name: string | null): void {
  const show = (title: string, content: string): void => {
    busy(body, true);
    let draft = content;
    let filename = name ?? '';
    const status = el('span', {});

    const save = (): void => {
      const target = filename.trim();
      if (!target) {
        flash(status, 'A note needs a name.', 'error');
        return;
      }
      void api
        .saveNote(target, draft)
        .then((saved) => {
          filename = saved.name;
          flash(status, `Saved as ${saved.name}.`);
        })
        .catch((error: unknown) => {
          flash(status, reason(error), 'error');
        });
    };

    const editor = textArea(content, (value) => {
      draft = value;
    });

    replace(
      body,
      el(
        'div',
        { class: 'row spaced' },
        button('arrow-left', 'All notes', () => {
          renderNotes(body);
        }),
        status,
      ),
      name === null
        ? control(
            { label: 'Name' },
            textField(
              '',
              (value) => {
                filename = value;
              },
              'shopping list',
            ),
          )
        : el('h3', {}, title),
      editor,
      el(
        'div',
        { class: 'row spaced' },
        button('save', 'Save', save),
        name === null
          ? null
          : button(
              'trash',
              'Delete',
              () => {
                void ask({
                  title: `Delete "${name}"?`,
                  body: 'The note is removed from the notes folder. This cannot be undone.',
                  confirm: 'Delete',
                  danger: true,
                }).then((yes) => {
                  if (!yes) return;
                  void api
                    .deleteNote(name)
                    .then(() => {
                      renderNotes(body);
                    })
                    .catch((error: unknown) => {
                      flash(status, reason(error), 'error');
                    });
                });
              },
              'danger',
            ),
      ),
    );
  };

  if (name === null) {
    show('New note', '');
    return;
  }
  loading(body);
  void api
    .note(name)
    .then((note) => {
      show(note.name, note.content);
    })
    .catch((error: unknown) => {
      failed(body, error);
    });
}

function modifiedAgo(modified: number): string {
  return `${relativeTime(Date.now() / 1000 - modified)} ago`;
}

/* ----------------------------------------------------------------- timers */

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

/* --------------------------------------------------------------- settings */

// Saving can change the talk key, and the running listener has to hear about
// it. A callback rather than an import, because main.ts imports this module.
let afterSave: () => void = () => {
  /* nothing until main.ts registers one */
};

export function onSettingsSaved(handler: () => void): void {
  afterSave = handler;
}

export function renderSettings(body: HTMLElement): void {
  loading(body);
  void api
    .settings()
    .then((state: SettingsState) => {
      paintSettings(body, state);
    })
    .catch((error: unknown) => {
      failed(body, error);
    });
}

function paintSettings(body: HTMLElement, state: SettingsState): void {
  busy(body, false);
  // Only what the user actually touched is sent, so a save never rewrites a
  // value someone else changed in the file since this panel was opened.
  const pending = new Map<string, string | number | boolean>();
  const status = el('span', {});
  const saveButton = button('save', 'Save changes', () => {
    void submit();
  });
  saveButton.disabled = true;

  const touch = (key: string, value: string | number | boolean): void => {
    const field = state.fields.find((f) => f.key === key);
    if (field && field.value === value) pending.delete(key);
    else pending.set(key, value);
    saveButton.disabled = pending.size === 0;
    busy(body, pending.size > 0);
    replace(
      status,
      pending.size > 0
        ? el('span', { class: 'muted' }, `${pending.size} unsaved`)
        : null,
    );
  };

  const submit = async (): Promise<void> => {
    saveButton.disabled = true;
    try {
      const result = await api.saveSettings(Object.fromEntries(pending));
      afterSave();
      paintSettings(body, { ...state, fields: result.fields });
      // Re-find the status element: paintSettings replaced the whole body.
      const note = body.querySelector<HTMLElement>('.settings-status');
      if (note) {
        flash(
          note,
          result.needs_model_reload.length > 0
            ? 'Saved. The hearing settings take effect the next time the models load.'
            : 'Saved.',
        );
      }
    } catch (error) {
      saveButton.disabled = false;
      flash(status, reason(error), 'error');
    }
  };

  const groups = state.groups.map((group) =>
    el(
      'section',
      {},
      el('h3', {}, group),
      ...state.fields
        .filter((f) => f.group === group)
        .map((f) => renderField(f, touch)),
    ),
  );

  replace(
    body,
    el('div', { class: 'settings-status' }),
    ...groups,
    el('h3', {}, 'Files'),
    ...Object.values(state.files).map((path) =>
      el('div', { class: 'muted mono ellipsis' }, path),
    ),
    el(
      'div',
      { class: 'sticky-actions' },
      saveButton,
      button('rotate-ccw', 'Revert', () => {
        rerender(body, renderSettings);
      }),
      el('div', { class: 'spacer' }),
      status,
    ),
  );
}

function renderField(
  field: SettingField,
  touch: (key: string, value: string | number | boolean) => void,
): HTMLElement {
  const spec = {
    label: field.label,
    help: field.help,
    // Everything else is read afresh per request, so saying so on those would
    // be noise; the exception is worth a word.
    ...(field.applies === 'models' ? { badge: 'on next load' } : {}),
  };
  const onChange = (value: string | number | boolean): void => {
    touch(field.key, value);
  };

  // A chain rather than a switch: one lint rule wants every union member
  // spelled out as a case, another wants a return after the switch, and the
  // two cannot both be satisfied for an exhaustive one.
  if (field.kind === 'key') {
    return control(spec, keyField(String(field.value ?? ''), onChange));
  }
  if (field.kind === 'bool')
    return control(spec, toggle(field.value === true, onChange));
  if (field.kind === 'choice') {
    return control(
      spec,
      choiceField(String(field.value ?? ''), field.options, onChange),
    );
  }
  if (field.kind === 'int' || field.kind === 'float') {
    return control(
      spec,
      numberField(typeof field.value === 'number' ? field.value : 0, onChange, {
        min: field.minimum,
        max: field.maximum,
        step: field.step ?? (field.kind === 'int' ? 1 : 0.1),
      }),
    );
  }
  return control(spec, textField(String(field.value ?? ''), onChange));
}

/* ------------------------------------------------------------ connections */

/** Services with a logo of their own, vendored by scripts/vendor-brands.mjs. */
const BRAND_LOGOS = new Set(['telegram', 'calendar', 'spotify']);

/** The service's own logo, or for one without (the weather), a drawn one. */
function connectionLogo(name: string): HTMLElement {
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

export function renderConnections(body: HTMLElement): void {
  loading(body);
  void Promise.all([api.connections(), api.mcp()])
    .then(([state, mcp]: [ConnectionsState, McpState]) => {
      busy(body, false);
      replace(
        body,
        el(
          'p',
          { class: 'muted' },
          'Services outside this PC. Each is off until you switch it on, and nothing goes out while it is off. Tokens are kept in Windows Credential Manager, not in the settings file.',
        ),
        ...state.connections.map((connection) => connectionCard(body, connection)),
        mcpSection(body, mcp),
      );
    })
    .catch((error: unknown) => {
      failed(body, error);
    });
}

function statusLine(connection: ConnectionInfo): HTMLElement {
  const { ok, text } = connection.status;
  const kind = ok === true ? 'on' : ok === false ? 'bad' : '';
  return el(
    'div',
    { class: 'conn-status' },
    el('span', { class: `dot ${kind}`.trim() }),
    el('span', {}, text),
  );
}

function connectionCard(body: HTMLElement, connection: ConnectionInfo): HTMLElement {
  const { name } = connection;
  const status = el('span', {});
  const fields = new Map<string, string | number | boolean>();
  const typedSecrets = new Map<string, string>();

  const again = (): void => {
    rerender(body, renderConnections);
  };
  const failedWith = (error: unknown): void => {
    flash(status, reason(error), 'error');
  };
  const edited = (): void => {
    busy(body, fields.size > 0 || typedSecrets.size > 0);
  };

  /** Fields first, then secrets, then a check that it all works. */
  const saveAndCheck = async (): Promise<void> => {
    try {
      if (fields.size > 0) await api.saveSettings(Object.fromEntries(fields));
      for (const [key, value] of typedSecrets) {
        await api.saveSecret(name, key, value);
      }
      fields.clear();
      typedSecrets.clear();
      edited();
      await api.testConnection(name);
      again();
    } catch (error) {
      failedWith(error);
    }
  };

  const toggleOn = toggle(connection.on, (value) => {
    void api
      .saveSettings({ [`settings.connections.${name}.enabled`]: value })
      .then(again)
      .catch(failedWith);
  });

  const inputs = connection.fields.map((field) => {
    const onChange = (value: string | number | boolean): void => {
      if (value === field.value) fields.delete(field.key);
      else fields.set(field.key, value);
      edited();
    };
    if (field.kind === 'bool') {
      return control(
        { label: field.label, help: field.help },
        toggle(field.value === true, onChange),
      );
    }
    return control(
      { label: field.label, help: field.help },
      textField(String(field.value ?? ''), onChange),
    );
  });

  const secretInputs = connection.secrets
    .filter((secret) => secret.typed)
    .map((secret) => {
      const input = el('input', {
        class: 'input',
        type: 'password',
        autocomplete: 'off',
        placeholder: secret.set ? 'saved, paste to replace' : '',
      });
      input.addEventListener('input', () => {
        if (input.value.trim()) typedSecrets.set(secret.key, input.value.trim());
        else typedSecrets.delete(secret.key);
        edited();
      });
      return control({ label: secret.label, help: secret.help }, input);
    });

  const signedIn = connection.secrets.find((secret) => !secret.typed);
  const account = signedIn
    ? el(
        'div',
        { class: 'row' },
        icon(signedIn.set ? 'circle-check' : 'circle', 'sm'),
        el(
          'span',
          { class: signedIn.set ? '' : 'muted' },
          `${signedIn.label}: ${signedIn.set ? 'connected' : 'not connected'}`,
        ),
      )
    : null;

  const link = el('div', {});
  const actions = el(
    'div',
    { class: 'row' },
    button('check', 'Save and check', () => {
      void saveAndCheck();
    }),
    connection.signs_in
      ? button(
          'external-link',
          signedIn?.set === true ? 'Reconnect' : 'Connect',
          () => {
            void api
              .connect(name)
              .then((result) => {
                // The server opens the browser itself. The link is here for
                // when it could not, or opened it somewhere unexpected.
                if (result.url !== null && result.url !== '') {
                  replace(
                    link,
                    el(
                      'p',
                      { class: 'muted' },
                      'Finish in your browser. If nothing opened, ',
                      el('a', { href: result.url, target: '_blank' }, 'open this link'),
                      '.',
                    ),
                  );
                }
              })
              .catch(failedWith);
          },
        )
      : null,
    connection.configured || signedIn?.set === true
      ? button(
          'unplug',
          'Disconnect',
          () => {
            void ask({
              title: `Disconnect ${connection.label}?`,
              body: 'Its saved token is removed from Windows Credential Manager. Connecting again means signing in again.',
              confirm: 'Disconnect',
              danger: true,
              icon: 'unplug',
            }).then((yes) => {
              if (yes) void api.disconnect(name).then(again).catch(failedWith);
            });
          },
          'danger',
        )
      : null,
    status,
  );

  return el(
    'section',
    { class: connection.on ? 'conn' : 'conn off' },
    el(
      'div',
      { class: 'row spaced' },
      el('div', { class: 'row' }, connectionLogo(name), el('h3', {}, connection.label)),
      toggleOn,
    ),
    el('p', { class: 'muted' }, connection.blurb),
    connection.on ? statusLine(connection) : null,
    ...(connection.on
      ? [
          ...inputs,
          ...secretInputs,
          name === 'telegram' ? telegramPairing(connection, again, failedWith) : null,
          account,
          actions,
          link,
          el(
            'details',
            { class: 'guide' },
            el('summary', {}, 'How to set it up'),
            el(
              'ol',
              {},
              ...connection.guide.map((step) =>
                el(
                  'li',
                  {},
                  step.text,
                  step.url
                    ? el(
                        'a',
                        { href: step.url, target: '_blank', class: 'guide-link' },
                        icon('external-link', 'xs'),
                      )
                    : null,
                ),
              ),
            ),
          ),
        ]
      : []),
  );
}

function telegramPairing(
  connection: ConnectionInfo,
  again: () => void,
  failedWith: (error: unknown) => void,
): HTMLElement | null {
  if (connection.owner !== undefined && connection.owner !== 0) {
    return el(
      'div',
      { class: 'row' },
      icon('circle-check', 'sm'),
      el('span', {}, `Paired with chat ${String(connection.owner)}`),
    );
  }
  const candidates = connection.candidates ?? [];
  if (candidates.length === 0) {
    return el(
      'p',
      { class: 'muted' },
      connection.bot !== undefined && connection.bot !== ''
        ? `Send /start to @${connection.bot} from your own Telegram.`
        : 'Once the token is saved, send /start to your bot.',
    );
  }
  return el(
    'div',
    { class: 'list' },
    ...candidates.map((candidate) =>
      el(
        'div',
        { class: 'item row spaced' },
        el(
          'div',
          {},
          el('strong', {}, candidate.name),
          el(
            'div',
            { class: 'muted mono' },
            `${candidate.username ? `@${candidate.username} · ` : ''}chat ${String(candidate.chat_id)}`,
          ),
        ),
        button('check', "That's me", () => {
          void api.pairTelegram(candidate.chat_id).then(again).catch(failedWith);
        }),
      ),
    ),
  );
}

/* -------------------------------------------------------------------- MCP */

const MCP_STATUS: Record<McpServer['status'], { dot: string; text: string }> = {
  running: { dot: 'on', text: 'running' },
  starting: { dot: '', text: 'starting…' },
  stopped: { dot: '', text: 'off' },
  failed: { dot: 'bad', text: 'failed' },
};

function mcpSection(body: HTMLElement, state: McpState): HTMLElement {
  const again = (): void => {
    rerender(body, renderConnections);
  };
  return el(
    'section',
    { class: 'conn' },
    el(
      'div',
      { class: 'row spaced' },
      el('div', { class: 'row' }, connectionLogo('mcp'), el('h3', {}, 'MCP servers')),
    ),
    el(
      'p',
      { class: 'muted' },
      'Tools from other programs, through the Model Context Protocol. A server is a program Naka starts on this PC; add only ones you trust. Its tools ask before anything that is not read-only, and what they return is treated like a web page.',
    ),
    ...state.servers.map((server) => mcpCard(server, again)),
    state.servers.length === 0 ? el('p', { class: 'muted' }, 'None yet.') : null,
    mcpAdder(body, again),
    el('div', { class: 'muted mono ellipsis' }, state.file),
  );
}

function mcpCard(server: McpServer, again: () => void): HTMLElement {
  const status = el('span', {});
  const failedWith = (error: unknown): void => {
    flash(status, reason(error), 'error');
  };
  const shown = MCP_STATUS[server.status];
  const asking = server.tools.filter((tool) => tool.asks).length;
  const save = (changes: Partial<McpServerIn>): void => {
    void api
      .saveMcp(server.name, {
        command: server.command,
        args: server.args,
        env: {},
        confirm: server.confirm,
        disabled: !server.enabled,
        ...changes,
      })
      .then(again)
      .catch(failedWith);
  };
  return el(
    'div',
    { class: 'item mcp' },
    el(
      'div',
      { class: 'row spaced' },
      el('strong', {}, server.name),
      toggle(server.enabled, (value) => {
        save({ disabled: !value });
      }),
    ),
    el(
      'div',
      { class: 'conn-status' },
      el('span', { class: `dot ${shown.dot}`.trim() }),
      el(
        'span',
        {},
        server.status === 'running'
          ? `${String(server.tools.length)} tools · ${String(asking)} ask first`
          : server.error || shown.text,
      ),
    ),
    el(
      'div',
      {
        class: 'muted mono ellipsis',
        title: [server.command, ...server.args].join(' '),
      },
      [server.command, ...server.args].join(' '),
    ),
    server.tools.length > 0
      ? el(
          'details',
          { class: 'guide' },
          el('summary', {}, 'Tools'),
          el(
            'ul',
            { class: 'mcp-tools' },
            ...server.tools.map((tool) =>
              el(
                'li',
                {},
                el('span', { class: 'mono' }, tool.name),
                tool.asks ? el('span', { class: 'tag warn' }, 'asks first') : null,
                tool.description
                  ? el('div', { class: 'muted' }, tool.description)
                  : null,
              ),
            ),
          ),
        )
      : null,
    el(
      'div',
      { class: 'row' },
      control(
        { label: 'Ask before' },
        choiceField(server.confirm, ['writes', 'always', 'never'], (value) => {
          if (value === 'writes' || value === 'always' || value === 'never') {
            save({ confirm: value });
          }
        }),
      ),
    ),
    el(
      'div',
      { class: 'row' },
      button('refresh-cw', 'Restart', () => {
        void api.restartMcp(server.name).then(again).catch(failedWith);
      }),
      button(
        'trash',
        'Remove',
        () => {
          void ask({
            title: `Remove ${server.name}?`,
            body: 'The server is stopped and taken out of mcp.json, and its saved environment values are deleted.',
            confirm: 'Remove',
            danger: true,
          }).then((yes) => {
            if (yes) void api.deleteMcp(server.name).then(again).catch(failedWith);
          });
        },
        'danger',
      ),
      status,
    ),
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** One server from pasted JSON: Claude Desktop's shape, or just its body. */
function fromPasted(text: string): { name: string; spec: McpServerIn } | string {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return 'That is not valid JSON.';
  }
  if (!isRecord(data)) return 'Expected a JSON object.';
  const servers = isRecord(data.mcpServers)
    ? data.mcpServers
    : isRecord(data.servers)
      ? data.servers
      : data;
  for (const [name, spec] of Object.entries(servers)) {
    if (!isRecord(spec) || typeof spec.command !== 'string') continue;
    const env: Record<string, string> = {};
    if (isRecord(spec.env)) {
      for (const [key, value] of Object.entries(spec.env)) env[key] = String(value);
    }
    return {
      name,
      spec: {
        command: spec.command,
        args: Array.isArray(spec.args) ? spec.args.map(String) : [],
        env,
        confirm: 'writes',
        disabled: false,
      },
    };
  }
  return 'No server with a "command" in it.';
}

function mcpAdder(body: HTMLElement, again: () => void): HTMLElement {
  const status = el('span', {});
  let name = '';
  let command = '';
  let args = '';
  let env = '';
  let pasted = '';

  const add = (serverName: string, spec: McpServerIn): void => {
    const line = [spec.command, ...spec.args].join(' ');
    void ask({
      title: `Add ${serverName}?`,
      body: `Naka will start this program on your PC now and each time it starts: ${line}`,
      confirm: 'Add and start',
    }).then((yes) => {
      if (!yes) return;
      busy(body, false);
      void api
        .saveMcp(serverName, spec)
        .then(again)
        .catch((error: unknown) => {
          flash(status, reason(error), 'error');
        });
    });
  };

  const fromFields = (): void => {
    const vars: Record<string, string> = {};
    for (const line of env.split('\n')) {
      const at = line.indexOf('=');
      if (at > 0) vars[line.slice(0, at).trim()] = line.slice(at + 1).trim();
    }
    if (!name.trim() || !command.trim()) {
      flash(status, 'A server needs a name and a command.', 'error');
      return;
    }
    add(name.trim(), {
      command: command.trim(),
      args: args
        .split('\n')
        .map((arg) => arg.trim())
        .filter(Boolean),
      env: vars,
      confirm: 'writes',
      disabled: false,
    });
  };

  const typed = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    busy(
      body,
      [name, command, args, env, pasted].some((v) => v.trim() !== ''),
    );
  };

  return el(
    'details',
    { class: 'guide mcp-add' },
    el('summary', {}, 'Add a server'),
    control(
      {
        label: 'Paste its configuration',
        help: 'The JSON from the server’s instructions, as written for Claude Desktop ("mcpServers").',
      },
      textArea(
        '',
        typed((value) => {
          pasted = value;
        }),
      ),
    ),
    el(
      'div',
      { class: 'row' },
      button('plus', 'Add from JSON', () => {
        const parsed = fromPasted(pasted);
        if (typeof parsed === 'string') flash(status, parsed, 'error');
        else add(parsed.name, parsed.spec);
      }),
    ),
    el('p', { class: 'muted' }, 'Or fill it in:'),
    control(
      { label: 'Name' },
      textField(
        '',
        typed((value) => {
          name = value;
        }),
        'filesystem',
      ),
    ),
    control(
      { label: 'Command' },
      textField(
        '',
        typed((value) => {
          command = value;
        }),
        'npx',
      ),
    ),
    control(
      { label: 'Arguments', help: 'One per line.' },
      textArea(
        '',
        typed((value) => {
          args = value;
        }),
      ),
    ),
    control(
      {
        label: 'Environment',
        help: 'KEY=value, one per line. Stored in Windows Credential Manager.',
      },
      textArea(
        '',
        typed((value) => {
          env = value;
        }),
      ),
    ),
    el('div', { class: 'row' }, button('plus', 'Add', fromFields), status),
  );
}
