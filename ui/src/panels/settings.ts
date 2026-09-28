/** The Settings drawer: every field the server describes. */
import { api, type SettingField, type SettingsState } from '../api.js';
import { el, replace } from '../dom.js';
import {
  button,
  choiceField,
  control,
  keyField,
  numberField,
  textField,
  toggle,
} from '../ui.js';
import { reason, failed, loading, rerender, busy, flash } from './common.js';

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
