/**
 * The control panel.
 *
 * Layout follows the sketch: conversation on the left, the orb as the stage,
 * a Discord-style section rail on the right, and a strip of latest items
 * underneath. The rail opens a drawer rather than navigating, so the orb —
 * which is the live status indicator — is never hidden.
 */

import { api, type MemoryState, type OpsStatus, type ToolsState } from './api.js';
import { el, poll, relativeTime, replace } from './dom.js';
import { Orb, type OrbState } from './orb.js';
import { button, card, icon, panel, type IconName } from './ui.js';

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

function renderMemory(body: HTMLElement): void {
  replace(body, el('p', { class: 'muted' }, 'Loading…'));
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
        el(
          'p',
          { class: state.summary ? 'muted' : 'muted' },
          state.summary || 'Nothing folded in yet.',
        ),
        el(
          'div',
          { class: 'row' },
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
      replace(body, el('p', { class: 'error' }, String(error)));
    });
}

function renderTools(body: HTMLElement): void {
  replace(body, el('p', { class: 'muted' }, 'Loading…'));
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
            { class: 'turn' },
            el(
              'div',
              { class: 'row' },
              el('strong', {}, tool.name),
              tool.destructive
                ? el('span', { class: 'muted' }, '· needs confirmation')
                : null,
            ),
            el('div', { class: 'muted' }, tool.description),
          ),
        ),
      );
    })
    .catch((error: unknown) => {
      replace(body, el('p', { class: 'error' }, String(error)));
    });
}

function placeholder(what: string, why: string) {
  return (body: HTMLElement): void => {
    replace(body, el('p', {}, what), el('p', { class: 'muted' }, why));
  };
}

const SECTIONS: Section[] = [
  { id: 'memory', icon: 'brain', title: 'Memory', render: renderMemory },
  {
    id: 'notes',
    icon: 'notebook-pen',
    title: 'Notes',
    render: placeholder(
      'Notes are stored in ~/naka-notes.',
      'The server has no notes API yet — they are reachable only as tools. Next up.',
    ),
  },
  {
    id: 'timers',
    icon: 'clock',
    title: 'Timers',
    render: placeholder(
      'No timers running.',
      'Timers are recorded but never fire: there is no scheduler and no way to reach you yet.',
    ),
  },
  { id: 'tools', icon: 'wrench', title: 'Tools', render: renderTools },
  {
    id: 'settings',
    icon: 'sliders',
    title: 'Settings',
    render: placeholder(
      'Config lives in config/*.toml.',
      'Editing needs write endpoints with validation, which do not exist yet.',
    ),
  },
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

const VRAM_TOTAL_MB = 16303;

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
  const percent = Math.round((state.vram_used_mb / VRAM_TOTAL_MB) * 100);
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
    card('notebook-pen', 'Notes', '—', 'no API yet'),
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
  },
  () => {
    replace(convo.body, el('div', { class: 'empty' }, 'Cannot reach the server.'));
  },
);
