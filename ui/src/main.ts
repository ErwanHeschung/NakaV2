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
  renderMemory,
  renderNotes,
  renderSettings,
  renderTimers,
  renderTools,
} from './panels.js';
import { card, icon, panel, type IconName } from './ui.js';

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
topbar.append(brand, statusLine, el('div', { class: 'spacer' }));

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

/* ----------------------------------------------------------------- drawer */

interface Section {
  readonly id: string;
  readonly icon: IconName;
  readonly title: string;
  readonly render: (body: HTMLElement) => void;
}

let openSection: string | null = null;

function toggleSection(section: Section, trigger: HTMLElement): void {
  for (const other of rail.querySelectorAll('.icon-btn')) {
    other.classList.remove('active');
  }
  if (openSection === section.id) {
    openSection = null;
    drawer.classList.remove('open');
    return;
  }
  openSection = section.id;
  trigger.classList.add('active');

  const built = panel(section.icon, section.title);
  built.head.append(
    el('div', { class: 'spacer' }),
    (() => {
      const close = el('button', {
        class: 'icon-btn',
        title: 'Close',
        onclick: () => {
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
  section.render(built.body);
  replace(drawer, built.root);
  drawer.classList.add('open');
}

/* --------------------------------------------------------------- sections */

const SECTIONS: Section[] = [
  { id: 'memory', icon: 'brain', title: 'Memory', render: renderMemory },
  { id: 'notes', icon: 'notebook-pen', title: 'Notes', render: renderNotes },
  { id: 'timers', icon: 'clock', title: 'Timers', render: renderTimers },
  { id: 'tools', icon: 'wrench', title: 'Tools', render: renderTools },
  { id: 'settings', icon: 'sliders', title: 'Settings', render: renderSettings },
];

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
  if (!section || !trigger || openSection === section.id) return;
  toggleSection(section, trigger);
}

globalThis.addEventListener('hashchange', openFromHash);
openFromHash();

/* ------------------------------------------------------------ live status */

// Counts for the strip. They change slowly, so they ride the slower poll
// below rather than the two-second one that drives the orb.
let noteCount: number | null = null;
let timerCount: number | null = null;
let soonestTimer = '';

function describe(state: OpsStatus): { orbState: OrbState; what: string } {
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
    orbDetail.textContent =
      `${(state.vram_free_mb / 1024).toFixed(1)} GB free` +
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
    orbWhat.textContent = 'Server unreachable';
    orbDetail.textContent = String(error).slice(0, 80);
  },
);

// The conversation changes only when someone speaks, so it is polled far less
// often than the status strip.
poll(
  6000,
  async () => {
    renderConversation(await api.memory());
    const [notes, timers] = await Promise.all([api.notes(), api.timers()]);
    noteCount = notes.notes.length;
    timerCount = timers.timers.length;
    const next = timers.timers[0];
    soonestTimer = next
      ? `${next.label} in ${relativeTime(next.remaining_seconds)}`
      : '';
  },
  () => {
    replace(convo.body, el('div', { class: 'empty' }, 'Cannot reach the server.'));
  },
);
