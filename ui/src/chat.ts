/**
 * The conversation: the whole history, loaded as you scroll back, and the
 * turn in progress as it happens.
 *
 * History comes a page at a time from /conversation, newest first on screen
 * at the bottom. The scroller is a column-reverse flex box around a normal
 * list, which makes the browser do the two things a chat needs: it opens at
 * the newest message, and prepending older ones above does not move what
 * you are reading (the scroll offset is measured from the bottom). A
 * sentinel at the top of the list, watched by an IntersectionObserver, asks
 * for the previous page before you reach it.
 *
 * The live turn is built from 'turn' events: what was heard the moment it is
 * transcribed, each sentence as she says it, and how it ended. Before this,
 * nothing appeared until the whole answer was over — including what she had
 * heard, which is the part worth checking while she is still thinking.
 */

import { api, type ConversationTurn } from './api.js';
import { el, replace } from './dom.js';
import type { ServerEvent, Via } from './events.js';
import { joinReply, markdown } from './markdown.js';
import { icon, type IconName } from './ui.js';

const VIA_ICON: Record<Via, IconName> = {
  voice: 'mic',
  text: 'keyboard',
  telegram: 'message-circle',
};

const PAGE = 20;

/** How a tool shows up under her reply: an icon and a few words. */
const TOOL_LABELS: Record<string, [IconName, string]> = {
  web_search: ['globe', 'searched the web'],
  fetch_page: ['globe', 'read a page'],
  run_command: ['terminal', 'ran a command'],
  write_file: ['file-text', 'wrote a file'],
  read_file: ['file-text', 'read a file'],
  find_files: ['folder-open', 'looked for files'],
  set_timer: ['clock', 'set a timer'],
  list_timers: ['clock', 'checked timers'],
  cancel_timer: ['clock', 'cancelled a timer'],
  write_note: ['notebook-pen', 'wrote a note'],
  read_note: ['notebook-pen', 'read a note'],
  search_notes: ['notebook-pen', 'searched notes'],
  delete_note: ['notebook-pen', 'deleted a note'],
  get_time: ['clock', 'checked the time'],
  gpu_status: ['cpu', 'checked the GPU'],
};

interface Options {
  /** Stop whatever is playing or running; the chat has already hung up. */
  onStop: () => void;
  /** How to talk, for the empty state and the composer's hint. */
  hint: () => string;
}

/** The DOM of one exchange, kept so live events can finish it. */
class TurnView {
  readonly root: HTMLElement;
  private readonly you: HTMLElement;
  private readonly her: HTMLElement;
  private readonly herMeta: HTMLElement;
  private readonly youMeta: HTMLElement;
  private text = '';

  constructor(readonly id: number) {
    this.you = el('div', { class: 'bubble' });
    this.youMeta = el('div', { class: 'meta' });
    this.her = el('div', { class: 'bubble' });
    this.herMeta = el('div', { class: 'meta' });
    this.root = el(
      'article',
      { class: 'turn', 'data-id': id },
      el('div', { class: 'msg you' }, this.you, this.youMeta),
      el('div', { class: 'msg her' }, this.her, this.herMeta),
    );
  }

  heard(text: string, via: Via, at?: string): void {
    replace(this.you, text || '…');
    replace(this.youMeta, icon(VIA_ICON[via], 'xs'), el('span', {}, stamp(at)));
  }

  /** Waiting on her first sentence. */
  thinking(): void {
    this.root.classList.add('live');
    replace(
      this.her,
      el(
        'span',
        { class: 'typing', 'aria-label': 'thinking' },
        el('i'),
        el('i'),
        el('i'),
      ),
    );
  }

  add(sentence: string): void {
    this.text = joinReply(this.text, sentence);
    replace(this.her, ...markdown(this.text));
  }

  finish(turn: Pick<ConversationTurn, 'naka' | 'tools' | 'interrupted'>): void {
    this.root.classList.remove('live');
    this.text = turn.naka;
    if (turn.naka) replace(this.her, ...markdown(turn.naka));
    else replace(this.her, el('span', { class: 'muted' }, 'nothing said'));
    this.her.parentElement?.classList.toggle('cut', turn.interrupted);
    replace(
      this.herMeta,
      ...tools(turn.tools),
      turn.interrupted ? el('span', { class: 'tag' }, 'interrupted') : null,
    );
  }
}

export class Chat {
  readonly root: HTMLElement;
  private readonly scroller: HTMLElement;
  private readonly list: HTMLElement;
  private readonly sentinel: HTMLElement;
  private readonly empty: HTMLElement;
  private readonly input: HTMLTextAreaElement;
  private readonly stopButton: HTMLButtonElement;
  private readonly views = new Map<number, TurnView>();
  private readonly live = new Set<number>();
  private oldest: number | undefined;
  private hasMore = false;
  private loading = false;
  private sending: AbortController | null = null;

  constructor(private readonly options: Options) {
    this.sentinel = el('div', { class: 'sentinel' });
    this.empty = el('div', { class: 'empty' });
    this.list = el('div', { class: 'chat-list' }, this.sentinel, this.empty);
    this.scroller = el('div', { class: 'chat-scroll' }, this.list);

    this.input = el('textarea', {
      class: 'chat-input',
      rows: 1,
      placeholder: 'Type to Naka…',
      'aria-label': 'Message',
    });
    this.input.addEventListener('keydown', (event) => {
      // Enter sends, Shift+Enter is a new line, the way every chat does it.
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        void this.send();
      }
    });
    this.input.addEventListener('input', () => {
      this.grow();
    });
    const sendButton = el('button', {
      class: 'icon-btn send',
      title: 'Send',
      'aria-label': 'Send',
      onclick: () => void this.send(),
    });
    sendButton.append(icon('send'));
    this.stopButton = el('button', {
      class: 'icon-btn stop',
      title: 'Stop her',
      'aria-label': 'Stop',
      onclick: () => {
        this.stop();
      },
    });
    this.stopButton.append(icon('stop'));
    const composer = el(
      'div',
      { class: 'composer' },
      this.input,
      this.stopButton,
      sendButton,
    );

    this.root = el('div', { class: 'chat' }, this.scroller, composer);
    new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) void this.older();
      },
      // Asked for well before the top is reached, so scrolling back rarely
      // has to wait on it.
      { root: this.scroller, rootMargin: '400px 0px 0px 0px' },
    ).observe(this.sentinel);
    this.paintEmpty();
    this.paintBusy();
  }

  /** The newest page. Also what a reconnect calls, to catch up. */
  async load(): Promise<void> {
    const page = await api.conversation(undefined, PAGE);
    if (this.views.size === 0) this.hasMore = page.has_more;
    for (const turn of page.turns) this.upsert(turn);
    this.oldest ??= page.turns[0]?.id;
    this.paintEmpty();
  }

  onEvent(event: ServerEvent): void {
    if (event.topic !== 'turn' || event.id === undefined) return;
    const id = event.id;
    if (event.phase === 'heard') {
      const view = this.view(id, true);
      view.heard(event.text ?? '', event.via ?? 'voice');
      view.thinking();
      this.live.add(id);
    } else if (event.phase === 'sentence') {
      this.view(id, true).add(event.text ?? '');
    } else if (event.phase === 'done') {
      this.live.delete(id);
      // The saved turn has what the events did not carry: which tools ran,
      // and the time it was kept.
      void api.conversation(id + 1, 1).then((page) => {
        const turn = page.turns.find((t) => t.id === id);
        if (turn) this.upsert(turn);
      });
    }
    this.paintEmpty();
    this.paintBusy();
  }

  /** Stop the answer: hang up a typed one, and let the page stop the rest. */
  stop(): void {
    this.sending?.abort();
    this.sending = null;
    this.options.onStop();
  }

  private async send(): Promise<void> {
    const text = this.input.value.trim();
    if (!text) return;
    this.input.value = '';
    this.grow();
    // A new message while she is answering one replaces it, the way the
    // talk key does.
    if (this.sending || this.live.size > 0) this.stop();
    const controller = new AbortController();
    this.sending = controller;
    this.paintBusy();
    try {
      // Read per message, so the tools switch applies without a reload.
      const { agentic } = await api.clientConfig();
      await api.say(text, agentic, controller.signal);
    } catch (error) {
      if (!controller.signal.aborted) {
        this.flash(error instanceof Error ? error.message : 'not sent');
      }
    } finally {
      if (this.sending === controller) this.sending = null;
      this.paintBusy();
    }
  }

  private async older(): Promise<void> {
    if (this.loading || !this.hasMore || this.oldest === undefined) return;
    this.loading = true;
    try {
      const page = await api.conversation(this.oldest, PAGE);
      this.hasMore = page.has_more;
      // Oldest last into the top, so each lands above the one after it.
      for (const turn of page.turns.toReversed()) this.upsert(turn, true);
      this.oldest = page.turns[0]?.id ?? this.oldest;
    } finally {
      this.loading = false;
    }
  }

  private upsert(turn: ConversationTurn, atTop = false): void {
    const view = this.view(turn.id, false, atTop);
    view.heard(turn.user, turn.via, turn.at);
    view.finish(turn);
  }

  /** The view for a turn, made and placed in id order if it is new. */
  private view(id: number, live: boolean, atTop = false): TurnView {
    const existing = this.views.get(id);
    if (existing) return existing;
    const view = new TurnView(id);
    this.views.set(id, view);
    if (atTop) {
      this.sentinel.after(view.root);
    } else {
      // Normally the newest; a late 'done' fetch can land after a newer
      // live turn, so find its place rather than assume the end.
      const later = [...this.views.values()]
        .filter((v) => v.id > id && v.root.isConnected)
        .toSorted((a, b) => a.id - b.id)[0];
      if (later) later.root.before(view.root);
      else this.list.append(view.root);
    }
    if (live) this.scrollToEnd();
    return view;
  }

  private scrollToEnd(): void {
    // In a column-reverse scroller the newest end is offset 0.
    this.scroller.scrollTop = 0;
  }

  private grow(): void {
    this.input.style.height = 'auto';
    const wanted = this.input.scrollHeight;
    this.input.style.height = `${Math.min(wanted, 140)}px`;
    // A scrollbar only once the text outgrows the box: on one line the
    // native one showed its arrows beside the placeholder.
    this.input.style.overflowY = wanted > 140 ? 'auto' : 'hidden';
  }

  private paintEmpty(): void {
    const none = this.views.size === 0;
    this.empty.hidden = !none;
    if (none) {
      replace(
        this.empty,
        el('div', {}, 'Nothing said yet.'),
        el('div', { class: 'muted' }, `${this.options.hint()}, or type below.`),
      );
    }
  }

  private paintBusy(): void {
    const busy = this.sending !== null || this.live.size > 0;
    this.stopButton.hidden = !busy;
  }

  private flash(message: string): void {
    const note = el('div', { class: 'chat-note' }, message);
    this.list.append(note);
    setTimeout(() => {
      note.remove();
    }, 5000);
  }
}

/* ------------------------------------------------------------ rendering */

/** Tool chips, repeats folded into a count. */
function tools(names: string[]): HTMLElement[] {
  const counts = new Map<string, number>();
  for (const name of names) counts.set(name, (counts.get(name) ?? 0) + 1);
  return [...counts].map(([name, count]) => {
    const [glyph, label] = TOOL_LABELS[name] ?? ['wrench', name.replaceAll('_', ' ')];
    return el(
      'span',
      { class: 'chip', title: name },
      icon(glyph, 'xs'),
      count > 1 ? `${label} ×${count}` : label,
    );
  });
}

/** "14:05" today, "Yesterday 14:05", else "23 Sep 14:05". */
function stamp(at?: string): string {
  const when = at === undefined || at === '' ? new Date() : new Date(at);
  if (Number.isNaN(when.getTime())) return '';
  const time = when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const today = new Date();
  const days = Math.round(
    (new Date(today.toDateString()).getTime() -
      new Date(when.toDateString()).getTime()) /
      86_400_000,
  );
  if (days === 0) return time;
  if (days === 1) return `Yesterday ${time}`;
  return `${when.toLocaleDateString([], { day: 'numeric', month: 'short' })} ${time}`;
}
