/**
 * The control panel.
 *
 * Layout follows the sketch: conversation on the left, the orb as the stage,
 * a Discord-style section rail on the right, and a strip of latest items
 * underneath. The rail opens a drawer rather than navigating, so the orb —
 * which is the live status indicator — is never hidden.
 */

import { api, type MemoryState, type OpsStatus } from './api.js';
import { el, poll, relativeTime, replace } from './dom.js';
import { Orb, type OrbState } from './orb.js';
import {
  onSettingsSaved,
  renderMemory,
  rerender,
  renderNotes,
  renderSettings,
  renderTimers,
  renderTools,
} from './panels.js';
import { listen, type Topic } from './events.js';
import { chime, ensureNotifications, notify } from './sound.js';
import { Talk, type TalkState } from './talk.js';
import { card, icon, iconButton, keyName, panel, type IconName } from './ui.js';

const root = document.querySelector<HTMLElement>('#app');
if (!root) throw new Error('missing #app');

/* ------------------------------------------------------------------ shell */

const convo = panel('audio-lines', 'Conversation', 'convo');
const stage = el('section', { class: 'stage' });
const latest = el('section', { class: 'latest' });
const rail = el('nav', { class: 'rail' });
const topbar = el('header', { class: 'topbar' });
const drawer = el('aside', { class: 'drawer' });

const brandMark = el('span', { class: 'mark' });
const brand = el('div', { class: 'brand' }, brandMark, 'Naka');
const statusLine = el('span', { class: 'muted' }, 'connecting…');
const talkHint = el('span', { class: 'muted talk-hint' }, '');
const micButton = iconButton('mic', 'Microphone', () => {
  void talk.toggle();
});
topbar.append(brand, statusLine, el('div', { class: 'spacer' }), talkHint, micButton);

const orbCanvas = el('canvas', { class: 'orb' });
const orbWhat = el('div', { class: 'what' }, 'Connecting');
const orbDetail = el('div', { class: 'detail' }, '');
stage.append(
  el(
    'div',
    { class: 'orb-wrap' },
    orbCanvas,
    el('div', { class: 'orb-state' }, orbWhat, orbDetail),
  ),
);

root.append(topbar, convo.root, stage, latest, rail, drawer);
const orb = new Orb(orbCanvas);

/* -------------------------------------------------------- push to talk */

/**
 * Inside the tray's window, the tray owns the microphone and the key.
 *
 * Arming here as well would record every utterance twice, and WebView2 asks
 * permission for the microphone in a way the host may never answer. So the
 * panel only shows what the tray is doing, relayed as 'client' events.
 */
const TALK_STATES: readonly string[] = [
  'off',
  'ready',
  'listening',
  'thinking',
  'speaking',
];

function isTalkState(value: string): value is TalkState {
  return TALK_STATES.includes(value);
}

const embedded = new URLSearchParams(globalThis.location.search).has('embedded');
let trayKey = '';

// What the panel is doing outranks what the server reports: while a turn is
// in flight the orb should follow this conversation, not the poll.
let talkState: TalkState = 'off';
let talkDetail = '';

const talk = new Talk({
  onState: (state, detail) => {
    talkState = state;
    talkDetail = detail ?? '';
    paintTalk();
  },
  onLevel: (level) => {
    orb.setLevel(level);
  },
  // The reply lands in the conversation as soon as the server has recorded
  // it, rather than up to six seconds later on the next poll.
  onTranscript: () => {
    setTimeout(() => {
      void api.memory().then(renderConversation);
    }, 400);
  },
});

function paintTalk(): void {
  if (embedded) {
    // style, not the hidden attribute: .icon-btn sets display, which wins.
    micButton.style.display = 'none';
    talkHint.textContent = trayKey
      ? `hold ${keyName(trayKey)} to talk, from anywhere`
      : '';
    return;
  }
  micButton.classList.toggle('active', talk.armed);
  micButton.classList.toggle('recording', talkState === 'listening');
  talkHint.textContent = talk.armed
    ? `hold ${keyName(talk.key)} to talk`
    : talkDetail || 'microphone off';
  if (talkDetail && talk.armed) talkHint.textContent = talkDetail;
}

// Deliberately not awaited at the top level: this can sit on a microphone
// permission prompt, and the status polling below must not wait for the user
// to answer it.
function readTrayKey(): void {
  void api
    .clientConfig()
    .then((config) => {
      trayKey = config.push_to_talk_key;
      paintTalk();
    })
    .catch(() => null);
}

if (embedded) {
  readTrayKey();
  onSettingsSaved(readTrayKey);
} else {
  // oxlint-disable-next-line unicorn/prefer-top-level-await
  void talk.start().then(paintTalk).catch(paintTalk);
  onSettingsSaved(() => {
    void talk.refresh();
  });
}

/* -------------------------------------------------------------- ringing */

const banner = el('div', { class: 'ring' });
root.append(banner);

function ring(label: string): void {
  void chime();
  notify('Timer', `${label} is up.`);
  replace(
    banner,
    icon('bell'),
    el('span', {}, `${label} is up.`),
    el('button', {
      class: 'icon-btn',
      title: 'Dismiss',
      onclick: () => {
        banner.classList.remove('show');
      },
    }),
  );
  banner.querySelector('.icon-btn')?.append(icon('x', 'sm'));
  banner.classList.add('show');
  // Long enough to catch across a room, short enough that a timer from an
  // hour ago is not still on screen.
  setTimeout(() => {
    banner.classList.remove('show');
  }, 30000);
}

/**
 * Read everything that is on screen again.
 *
 * Called whenever the stream has been away, because nothing is replayed to a
 * client that reconnects: a change made during the gap is one this panel
 * would otherwise never hear about, and the panel would sit there confidently
 * showing something that stopped being true a minute ago.
 */
function resync(): void {
  refreshOpenSection();
  void refreshCounts().catch(() => null);
  void api
    .memory()
    .then(renderConversation)
    .catch(() => null);
}

listen({
  onEvent: (event) => {
    // A timer coming due is the one event that carries something to do
    // besides re-reading; everything else is just "this changed".
    if (event.rang !== undefined) ring(event.rang);
    if (openSection?.topic === event.topic) refreshOpenSection();
    if (event.topic === 'conversation') void api.memory().then(renderConversation);
    if (event.topic === 'notes' || event.topic === 'timers') void refreshCounts();
    if (event.topic === 'client' && event.state !== undefined && embedded) {
      if (isTalkState(event.state)) talkState = event.state;
      paintTalk();
    }
    if (event.topic === 'settings' && embedded) readTrayKey();
  },
  onConnection: (live) => {
    // Only worth saying when it is not: a panel that cannot be reached will
    // not ring, and that is the kind of thing to find out before dinner.
    document.body.classList.toggle('offline', !live);
    // Every open is a reconnect after the first, and a reconnect means a
    // window of changes that were published to nobody.
    if (live) resync();
  },
});

// A backgrounded tab gets throttled and its connections dropped, so coming
// back to the panel is the other moment its contents cannot be trusted.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') resync();
});

/* ----------------------------------------------------------------- drawer */

interface Section {
  readonly id: string;
  readonly icon: IconName;
  readonly title: string;
  /**
   * What this section displays. When the server says that changed, the
   * section is re-rendered — which is the whole of how the panel stays
   * current. A section with no topic shows something that cannot change.
   */
  readonly topic?: Topic;
  /** May return a teardown, for a section that holds a timer. */
  readonly render: (body: HTMLElement) => void | (() => void);
}

let openSection: Section | null = null;
// Whatever the open section left running. Closing a drawer has to stop it:
// the panel is re-rendered from scratch each time it opens, so anything still
// ticking is working on nodes that are no longer on the page.
let closeSection: (() => void) | null = null;

function teardown(): void {
  closeSection?.();
  closeSection = null;
}

function toggleSection(section: Section, trigger: HTMLElement): void {
  for (const other of rail.querySelectorAll('.icon-btn')) {
    other.classList.remove('active');
  }
  teardown();
  if (openSection?.id === section.id) {
    openSection = null;
    drawer.classList.remove('open');
    return;
  }
  openSection = section;
  trigger.classList.add('active');

  const built = panel(section.icon, section.title);
  built.head.append(
    el('div', { class: 'spacer' }),
    (() => {
      const close = el('button', {
        class: 'icon-btn',
        title: 'Close',
        onclick: () => {
          teardown();
          openSection = null;
          drawer.classList.remove('open');
          for (const other of rail.querySelectorAll('.icon-btn')) {
            other.classList.remove('active');
          }
        },
      });
      close.append(icon('x', 'sm'));
      return close;
    })(),
  );
  closeSection = section.render(built.body) ?? null;
  replace(drawer, built.root);
  drawer.classList.add('open');
}

/* --------------------------------------------------------------- sections */

const SECTIONS: Section[] = [
  {
    id: 'memory',
    icon: 'brain',
    title: 'Memory',
    topic: 'memory',
    render: renderMemory,
  },
  {
    id: 'notes',
    icon: 'notebook-pen',
    title: 'Notes',
    topic: 'notes',
    render: renderNotes,
  },
  {
    id: 'timers',
    icon: 'clock',
    title: 'Timers',
    topic: 'timers',
    render: renderTimers,
  },
  // Listens to settings: the allowlist is fixed at startup, but the powers
  // that gate web and PowerShell are switched from here and from Settings.
  {
    id: 'tools',
    icon: 'wrench',
    title: 'Tools',
    topic: 'settings',
    render: renderTools,
  },
  {
    id: 'settings',
    icon: 'sliders',
    title: 'Settings',
    topic: 'settings',
    render: renderSettings,
  },
];

/**
 * Re-render the open section in place.
 *
 * Deliberately not a toggle: the drawer stays open, keeps its scroll position
 * where it can, and only the body is rebuilt.
 */
function refreshOpenSection(): void {
  const section = openSection;
  if (!section) return;
  const body = drawer.querySelector<HTMLElement>('.panel-body');
  if (!body) return;
  // A section that is holding unsaved input keeps it. Rebuilding under
  // someone mid-sentence to show them a note they did not change is not a
  // fresher panel, it is a lost draft.
  if (body.dataset.busy !== undefined) return;
  teardown();
  closeSection = rerender(body, section.render) ?? null;
}

const triggers = new Map<string, HTMLElement>();

function makeTrigger(section: Section): HTMLElement {
  const trigger = el('button', {
    class: 'icon-btn',
    title: section.title,
    'aria-label': section.title,
  });
  trigger.append(icon(section.icon));
  trigger.addEventListener('click', () => {
    toggleSection(section, trigger);
    globalThis.location.hash = openSection === null ? '' : `#${section.id}`;
  });
  return trigger;
}

for (const section of SECTIONS) {
  const trigger = makeTrigger(section);
  triggers.set(section.id, trigger);
  rail.append(trigger);
  // Settings sits apart from the content sections above it.
  if (section.id === 'tools') rail.append(el('div', { class: 'sep' }));
}

/** Open whatever #section the URL names, so a panel can be linked to. */
function openFromHash(): void {
  const id = globalThis.location.hash.replace('#', '');
  const section = SECTIONS.find((candidate) => candidate.id === id);
  const trigger = section ? triggers.get(section.id) : undefined;
  if (!section || !trigger || openSection?.id === section.id) return;
  toggleSection(section, trigger);
}

globalThis.addEventListener('hashchange', openFromHash);
openFromHash();

/* ------------------------------------------------------------ live status */

// How long after the page opens an unanswered status counts as starting
// rather than broken. A cold start has taken up to forty seconds.
const STARTUP_GRACE_MS = 90_000;

// Counts for the strip. They change slowly, so they ride the slower poll
// below rather than the two-second one that drives the orb.
let noteCount: number | null = null;
let timerCount: number | null = null;
let soonestTimer = '';

const TALKING: Partial<Record<TalkState, { orbState: OrbState; what: string }>> = {
  listening: { orbState: 'speaking', what: 'Listening to you' },
  thinking: { orbState: 'thinking', what: 'Thinking' },
  speaking: { orbState: 'speaking', what: 'Speaking' },
};

function describe(state: OpsStatus): { orbState: OrbState; what: string } {
  // Ahead of talking: a request made while the models load is waiting on the
  // load, and "Thinking" for twenty seconds says nothing about why.
  if (state.loading) {
    return { orbState: 'thinking', what: `Waking up · ${state.loading.step}` };
  }
  const talking = TALKING[talkState];
  if (talking) return talking;
  if (state.in_flight > 0) return { orbState: 'thinking', what: 'Working' };
  if (state.models_loaded && state.llm_up === true) {
    return { orbState: 'idle', what: 'Listening' };
  }
  if (state.models_loaded) return { orbState: 'idle', what: 'Half awake' };
  return { orbState: 'sleeping', what: 'Sleeping' };
}

function renderLatest(state: OpsStatus): void {
  const usedGb = (state.vram_used_mb / 1024).toFixed(1);
  const freeGb = (state.vram_free_mb / 1024).toFixed(1);
  const percent = Math.round((state.vram_used_mb / state.vram_total_mb) * 100);
  replace(
    latest,
    card('cpu', 'GPU memory', `${usedGb} GB`, `${freeGb} GB free · ${percent}%`),
    card(
      'zap',
      'Models',
      state.models_loaded ? 'Loaded' : 'Released',
      state.llm_up === true ? 'language model up' : 'language model stopped',
    ),
    card('clock', 'Idle', relativeTime(state.idle_seconds), 'since last request'),
    card(
      'notebook-pen',
      'Notes',
      noteCount === null ? '—' : String(noteCount),
      'in the notes folder',
    ),
    card(
      'bell',
      'Timers',
      timerCount === null ? '—' : String(timerCount),
      soonestTimer || 'none running',
    ),
  );
}

function renderConversation(state: MemoryState): void {
  if (state.recent.length === 0) {
    replace(convo.body, el('div', { class: 'empty' }, 'Nothing said yet.'));
    return;
  }
  replace(
    convo.body,
    ...state.recent.map((turn) =>
      el(
        'div',
        { class: 'turn' },
        el('div', { class: 'bubble you' }, turn.user),
        el('div', { class: 'bubble her' }, turn.naka),
      ),
    ),
  );
}

poll(
  2000,
  async () => {
    const state = await api.opsStatus();
    const { orbState, what } = describe(state);
    orb.setState(orbState);
    orbWhat.textContent = what;
    const load = state.loading;
    orbDetail.textContent = load
      ? `step ${load.index} of ${load.total} · ${load.seconds}s so far`
      : `${(state.vram_free_mb / 1024).toFixed(1)} GB free` +
        (state.in_flight > 0 ? ` · ${state.in_flight} in flight` : '');
    statusLine.textContent = '';
    statusLine.append(
      el('span', { class: `dot ${orbState === 'sleeping' ? '' : 'on'}` }),
      ` ${what.toLowerCase()}`,
    );
    renderLatest(state);
  },
  (error) => {
    orb.setState('sleeping');
    // The page can be open before the server is, and the first seconds of
    // a start are Python loading its libraries: that is not a failure.
    const starting = performance.now() < STARTUP_GRACE_MS;
    orbWhat.textContent = starting ? 'Starting Naka' : 'Server unreachable';
    orbDetail.textContent = starting
      ? `loading the speech libraries · ${Math.round(performance.now() / 1000)}s`
      : String(error).slice(0, 80);
  },
);

async function refreshCounts(): Promise<void> {
  const [notes, timers] = await Promise.all([api.notes(), api.timers()]);
  if (timers.timers.length > 0 && timerCount === 0) void ensureNotifications();
  noteCount = notes.notes.length;
  timerCount = timers.timers.length;
  const next = timers.timers[0];
  soonestTimer = next ? `${next.label} in ${relativeTime(next.remaining_seconds)}` : '';
}

// A backstop, not the mechanism. Changes arrive over the event stream within
// milliseconds of happening; this only covers the window where that stream is
// down and EventSource has not finished reconnecting yet.
poll(
  30000,
  async () => {
    renderConversation(await api.memory());
    // Cheap, and it means a tab left open picks up a settings change rather
    // than running forever on whatever it read when it loaded.
    void talk.refresh().then(paintTalk);
    await refreshCounts();
  },
  () => {
    replace(convo.body, el('div', { class: 'empty' }, 'Cannot reach the server.'));
  },
);
