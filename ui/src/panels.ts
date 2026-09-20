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
  type MemoryState,
  type NotesState,
  type SettingField,
  type SettingsState,
  type TimersState,
  type ToolsState,
} from './api.js';
import { el, relativeTime, replace } from './dom.js';
import {
  button,
  choiceField,
  control,
  icon,
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

function loading(body: HTMLElement): void {
  replace(body, el('p', { class: 'muted' }, 'Loading…'));
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
  void api
    .memory()
    .then((state: MemoryState) => {
      replace(
        body,
        el('h3', {}, `Facts — ${state.facts.length}`),
        state.facts.length > 0
          ? el('ol', { class: 'facts' }, ...state.facts.map((f) => el('li', {}, f)))
          : el('p', { class: 'muted' }, 'Nothing remembered yet.'),
        el('h3', {}, 'Summary'),
        el('p', { class: 'muted' }, state.summary || 'Nothing folded in yet.'),
        el(
          'div',
          { class: 'row spaced' },
          button('refresh-cw', 'Refresh', () => {
            renderMemory(body);
          }),
          button(
            'trash',
            'Clear conversation',
            () => {
              if (!confirm('Clear the conversation? Remembered facts are kept.'))
                return;
              void api.forgetConversation().then(() => {
                renderMemory(body);
              });
            },
            'danger',
          ),
        ),
      );
    })
    .catch((error: unknown) => {
      failed(body, error);
    });
}

/* ------------------------------------------------------------------ tools */

export function renderTools(body: HTMLElement): void {
  loading(body);
  void api
    .tools()
    .then((state: ToolsState) => {
      replace(
        body,
        el(
          'p',
          { class: 'muted' },
          `${state.allowed.length} allowed · max ${state.max_steps} steps · ${state.timeout_s}s timeout`,
        ),
        ...state.allowed.map((tool) =>
          el(
            'div',
            { class: 'item' },
            el(
              'div',
              { class: 'row' },
              el('strong', {}, tool.name),
              tool.destructive
                ? el('span', { class: 'tag warn' }, 'confirms first')
                : null,
            ),
            el('div', { class: 'muted' }, tool.description),
          ),
        ),
      );
    })
    .catch((error: unknown) => {
      failed(body, error);
    });
}

/* ------------------------------------------------------------------ notes */

export function renderNotes(body: HTMLElement): void {
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
                if (!confirm(`Delete the note "${name}"? This cannot be undone.`))
                  return;
                void api
                  .deleteNote(name)
                  .then(() => {
                    renderNotes(body);
                  })
                  .catch((error: unknown) => {
                    flash(status, reason(error), 'error');
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

export function renderTimers(body: HTMLElement): void {
  loading(body);
  void api
    .timers()
    .then((state: TimersState) => {
      // The server's clock is the one the timers were set against, so the
      // countdown is driven from the offset rather than from Date.now()
      // directly — otherwise a clock skew shows up as wrong time remaining.
      const offset = state.now - Date.now() / 1000;
      let label = '';
      let minutes = 5;
      const status = el('span', {});

      const rows = state.timers.map((timer) => {
        const left = el('span', { class: 'value mono' }, '');
        const update = (): boolean => {
          const remaining = Math.round(timer.due - (Date.now() / 1000 + offset));
          left.textContent = remaining > 0 ? countdown(remaining) : 'done';
          return remaining > 0;
        };
        update();
        const handle = setInterval(() => {
          // Stop ticking once it lands, and let the poll below replace the
          // list. Left running, this would count into negative numbers.
          if (!update()) clearInterval(handle);
        }, 1000);
        return el(
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
            left,
            button(
              'x',
              'Cancel',
              () => {
                clearInterval(handle);
                void api
                  .cancelTimer(timer.label)
                  .then(() => {
                    renderTimers(body);
                  })
                  .catch((error: unknown) => {
                    flash(status, reason(error), 'error');
                  });
              },
              'danger',
            ),
          ),
        );
      });

      replace(
        body,
        state.can_fire
          ? null
          : el(
              'div',
              { class: 'warning' },
              icon('triangle-alert', 'sm'),
              el('span', {}, state.note),
            ),
        rows.length > 0
          ? el('div', { class: 'list' }, ...rows)
          : el('p', { class: 'muted' }, 'No timers running.'),
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
            { min: 1, max: 1440 },
          ),
        ),
        el(
          'div',
          { class: 'row spaced' },
          button('circle-plus', 'Start', () => {
            if (!label.trim()) {
              flash(status, 'A timer needs a name.', 'error');
              return;
            }
            void api
              .startTimer(label.trim(), Math.round(minutes * 60))
              .then(() => {
                renderTimers(body);
              })
              .catch((error: unknown) => {
                flash(status, reason(error), 'error');
              });
          }),
          status,
        ),
      );
    })
    .catch((error: unknown) => {
      failed(body, error);
    });
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
        renderSettings(body);
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
