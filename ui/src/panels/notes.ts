/** The Notes drawer: the notes folder, and an editor. */
import { api, type NotesState } from '../api.js';
import { el, relativeTime, replace } from '../dom.js';
import { ask, button, control, icon, textArea, textField } from '../ui.js';
import { reason, failed, loading, busy, flash } from './common.js';

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
