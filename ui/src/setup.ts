/**
 * The first-run wizard.
 *
 * The same page furniture as the panel — its background, orb, icons and
 * controls — so the first thing someone sees of Naka already looks like Naka.
 * It talks to setup/wizard.py through pywebview's bridge, not over HTTP: no
 * server exists yet when this runs.
 *
 * Screens, in order: the card check, a few questions, the model, the install
 * itself, and done. A setup that stopped halfway reopens straight on the
 * install screen and carries on.
 */

import { el, replace } from './dom.js';
import { Orb, type OrbState } from './orb.js';
import {
  button,
  choiceField,
  control,
  icon,
  keyField,
  keyName,
  textField,
  toggle,
  type IconName,
} from './ui.js';

/* ------------------------------------------------------------ the bridge */

interface GpuVerdict {
  ok: boolean;
  reason?: string;
  warning?: string | null;
  name?: string;
  vram_mb?: number;
  driver?: string;
}

interface ModelEntry {
  id: string;
  name: string;
  file: string;
  bytes?: number;
  vram_mb?: number;
  notes?: string;
  recommended?: boolean;
  fits: boolean;
}

interface StepInfo {
  id: string;
  title: string;
  status: string;
  detail: string;
}

interface Identity {
  assistant: string;
  user: string;
  user_pronoun: string;
  user_possessive: string;
}

interface Plan {
  gpu: GpuVerdict;
  models: ModelEntry[];
  identity: Identity;
  push_to_talk_key: string;
  steps: StepInfo[];
  choices: { model?: string };
  complete: boolean;
}

interface Browsed {
  path: string;
  name: string;
  ok: boolean;
  reason?: string;
  bytes?: number;
}

interface LinkCheck {
  ok: boolean;
  reason?: string;
  bytes?: number | null;
  resumable?: boolean;
}

interface Choices {
  model: string;
  identity: Identity;
  push_to_talk_key: string;
  autostart: boolean;
}

interface WizardApi {
  plan(): Promise<Plan>;
  browse(): Promise<Browsed | null>;
  check_link(url: string): Promise<LinkCheck>;
  start(choices: Choices): Promise<void>;
  retry(): Promise<void>;
  open_logs(): Promise<void>;
  diagnostics(): Promise<string>;
  finish(): Promise<void>;
}

interface SetupEvent {
  kind: 'step' | 'progress' | 'done';
  step?: string;
  status?: string | null;
  done?: number | null;
  total?: number | null;
  message?: string;
  ok?: boolean;
  complete?: boolean;
}

declare global {
  interface Window {
    pywebview?: { api: WizardApi };
    nakaSetup?: (event: SetupEvent) => void;
  }
}

/* ----------------------------------------------------------------- shell */

const found = document.querySelector<HTMLElement>('#wizard');
if (!found) throw new Error('missing #wizard');
const root: HTMLElement = found;

const orbCanvas = el('canvas', { class: 'orb wizard-orb' });
const heading = el('h1', { class: 'wizard-title' }, '');
const lede = el('p', { class: 'wizard-lede' }, '');
const body = el('section', { class: 'wizard-body' });
const footer = el('footer', { class: 'wizard-footer' });

root.append(
  el('div', { class: 'wizard-hero' }, orbCanvas, heading, lede),
  el('div', { class: 'panel wizard-card' }, body, footer),
);
const orb = new Orb(orbCanvas);

function screen(
  state: OrbState,
  title: string,
  subtitle: string,
  content: HTMLElement[],
  actions: (HTMLElement | null)[] = [],
): void {
  orb.setState(state);
  heading.textContent = title;
  lede.textContent = subtitle;
  replace(body, ...content);
  replace(footer, el('div', { class: 'spacer' }), ...actions);
  footer.hidden = actions.every((a) => a === null);
  // The install screen has a dozen rows to show; a smaller orb keeps them
  // all in the window without scrolling.
  root.classList.toggle('compact', state === 'thinking');
}

/** Messages from Python start lower-case, to read inside a sentence. */
function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function primary(
  name: IconName | null,
  label: string,
  onClick: () => void,
): HTMLButtonElement {
  return button(name, label, onClick, 'primary');
}

function gb(bytes: number | null | undefined): string {
  return bytes !== null && bytes !== undefined && bytes > 0
    ? `${(bytes / 1e9).toFixed(1)} GB`
    : 'size unknown';
}

/* ---------------------------------------------------------------- flow */

let api: WizardApi;
let plan: Plan;

const choices: Choices = {
  model: '',
  identity: {
    assistant: 'Naka',
    user: '',
    user_pronoun: 'they',
    user_possessive: 'their',
  },
  push_to_talk_key: 'ControlRight',
  autostart: true,
};

function begin(): void {
  if (!window.pywebview) {
    screen('sleeping', 'Setup', 'This page runs inside the Naka setup window.', []);
    return;
  }
  api = window.pywebview.api;
  void api.plan().then((loaded) => {
    plan = loaded;
    choices.identity = { ...choices.identity, ...stripEmpty(loaded.identity) };
    choices.push_to_talk_key = loaded.push_to_talk_key;
    if (loaded.complete) {
      showDone();
    } else if (
      (loaded.choices.model ?? '') !== '' &&
      loaded.steps.some((s) => s.status === 'ok')
    ) {
      // Stopped partway through an earlier run: go straight back to it.
      choices.model = loaded.choices.model ?? '';
      showInstall(false);
    } else {
      showCheck();
    }
  });
}

function stripEmpty(identity: Identity): Partial<Identity> {
  return Object.fromEntries(Object.entries(identity).filter(([, v]) => v !== ''));
}

/* 1 — the card ------------------------------------------------------------ */

function showCheck(): void {
  const gpu = plan.gpu;
  if (!gpu.ok) {
    showBlocked(gpu.reason ?? 'This machine cannot run Naka.');
    return;
  }
  screen(
    'idle',
    'Hello.',
    'Naka runs entirely on your own machine. Nothing you say leaves it.',
    [
      el(
        'div',
        { class: 'fact-card good' },
        icon('cpu'),
        el(
          'div',
          {},
          el('strong', {}, gpu.name ?? 'Graphics card'),
          el(
            'div',
            { class: 'muted' },
            `${Math.round((gpu.vram_mb ?? 0) / 1024)} GB · driver ${gpu.driver ?? '?'}`,
          ),
        ),
        icon('circle-check', 'ok-mark'),
      ),
      (gpu.warning ?? '') === ''
        ? el('p', { class: 'muted' }, 'Your card can run Naka comfortably.')
        : el(
            'div',
            { class: 'warning' },
            icon('triangle-alert', 'sm'),
            el('span', {}, gpu.warning ?? ''),
          ),
      el(
        'p',
        { class: 'muted' },
        'Setup downloads about 15 GB: the runtime, the speech models and a ' +
          'language model. It can be interrupted and will pick up where it stopped.',
      ),
    ],
    [primary('sparkles', 'Get started', showAbout)],
  );
}

function showBlocked(reason: string): void {
  const copied = el('span', { class: 'muted' }, '');
  screen(
    'sleeping',
    'Naka cannot run here.',
    'Nothing has been downloaded.',
    [
      el(
        'div',
        { class: 'warning' },
        icon('circle-alert', 'sm'),
        el('span', {}, reason),
      ),
    ],
    [
      copied,
      button('file-text', 'Copy details', () => {
        void api.diagnostics().then((text) => {
          void navigator.clipboard.writeText(text).then(() => {
            copied.textContent = 'Copied.';
          });
        });
      }),
    ],
  );
}

/* 2 — about you ------------------------------------------------------------ */

const PRONOUNS: Record<string, string> = { he: 'his', she: 'her', they: 'their' };

function showAbout(): void {
  const id = choices.identity;
  screen(
    'idle',
    'A little about you.',
    'So she knows who she is talking to. All of this can be changed later in Settings.',
    [
      el(
        'div',
        { class: 'form-grid' },
        control(
          { label: 'Your name' },
          textField(
            id.user,
            (v) => {
              id.user = v.trim();
            },
            'What she should call you',
          ),
        ),
        control(
          { label: 'Refer to you as' },
          choiceField(
            id.user_pronoun in PRONOUNS ? id.user_pronoun : 'they',
            Object.keys(PRONOUNS),
            (v) => {
              id.user_pronoun = v;
              id.user_possessive = PRONOUNS[v] ?? 'their';
            },
          ),
        ),
        control(
          { label: 'Her name' },
          textField(id.assistant, (v) => {
            id.assistant = v.trim() || 'Naka';
          }),
        ),
        control(
          {
            label: 'Push to talk',
            help: 'Hold it anywhere on your computer to speak. Click, then press the key.',
          },
          keyField(choices.push_to_talk_key, (code) => {
            choices.push_to_talk_key = code;
          }),
        ),
      ),
      control(
        {
          label: 'Start with Windows',
          help: 'Naka waits in the tray. Nothing is loaded onto your graphics card until you speak.',
        },
        toggle(choices.autostart, (on) => {
          choices.autostart = on;
        }),
      ),
    ],
    [button('arrow-left', 'Back', showCheck), primary(null, 'Continue', showModel)],
  );
}

/* 3 — the model ------------------------------------------------------------ */

function showModel(): void {
  const status = el('p', { class: 'muted' }, '');
  let selected = choices.model;

  const next = primary(null, 'Install', () => {
    choices.model = selected;
    showInstall(true);
  });
  const refresh = (): void => {
    next.disabled = selected === '';
    for (const node of body.querySelectorAll<HTMLElement>('.model-card')) {
      node.classList.toggle('selected', node.dataset.choice === selected);
    }
  };

  const cards = plan.models.map((model) => {
    const node = el(
      'button',
      {
        class: `model-card${model.fits ? '' : ' disabled'}`,
        type: 'button',
        'data-choice': model.id,
        onclick: () => {
          if (!model.fits) return;
          selected = model.id;
          refresh();
        },
      },
      el(
        'div',
        { class: 'row' },
        el('strong', {}, model.name),
        model.recommended === true ? el('span', { class: 'tag' }, 'recommended') : null,
        el('div', { class: 'spacer' }),
        el('span', { class: 'muted' }, gb(model.bytes)),
      ),
      el(
        'div',
        { class: 'muted' },
        model.fits ? (model.notes ?? '') : 'Too large for your graphics card.',
      ),
    );
    if (!model.fits) node.setAttribute('disabled', '');
    return node;
  });

  // A file already on this machine.
  const local = el('button', {
    class: 'model-card',
    type: 'button',
    'data-choice': '__local',
  });
  const paintLocal = (label: string, detail: string): void => {
    replace(
      local,
      el('div', { class: 'row' }, icon('folder-open', 'sm'), el('strong', {}, label)),
      el('div', { class: 'muted' }, detail),
    );
  };
  paintLocal(
    'Use a model I already have',
    'Choose a .gguf file on this computer. It is used where it is, not copied.',
  );
  local.addEventListener('click', () => {
    void api.browse().then((picked) => {
      if (!picked) return;
      if (!picked.ok) {
        status.textContent = `That file cannot be used: ${picked.reason ?? 'unknown reason'}.`;
        return;
      }
      selected = picked.path;
      local.dataset.choice = picked.path;
      paintLocal(picked.name, `${gb(picked.bytes)} · ${picked.path}`);
      status.textContent = '';
      refresh();
    });
  });

  // A link to paste.
  let link = '';
  const linkCard = el(
    'div',
    { class: 'model-card link-card', 'data-choice': '__link' },
    el(
      'div',
      { class: 'row' },
      icon('link', 'sm'),
      el('strong', {}, 'Download from a link'),
    ),
    el(
      'div',
      { class: 'row' },
      textField(
        '',
        (v) => {
          link = v.trim();
        },
        'https://huggingface.co/…/resolve/main/model.gguf',
      ),
      button('check', 'Check', () => {
        status.textContent = 'Checking the link…';
        void api.check_link(link).then((result) => {
          if (!result.ok) {
            status.textContent =
              sentence(result.reason ?? 'that link does not work') + '.';
            return;
          }
          selected = link;
          linkCard.dataset.choice = link;
          status.textContent =
            `Found, ${gb(result.bytes)}.` +
            (result.resumable === false
              ? ' This server cannot resume an interrupted download.'
              : '');
          refresh();
        });
      }),
    ),
  );

  screen(
    'idle',
    'Choose her mind.',
    'The language model does the thinking. You can switch later without reinstalling.',
    [el('div', { class: 'model-list' }, ...cards, local, linkCard), status],
    [button('arrow-left', 'Back', showAbout), next],
  );
  refresh();
}

/* 4 — the install ---------------------------------------------------------- */

function showInstall(fresh: boolean): void {
  const rows = new Map<string, HTMLElement>();
  const list = el('ol', { class: 'step-list' });
  for (const step of plan.steps) {
    const row = el('li', { class: `step ${step.status}` });
    paintStep(row, step.title, step.status, step.detail);
    rows.set(step.id, row);
    list.append(row);
  }

  const bar = el('div', { class: 'meter-fill' });
  const meter = el('div', { class: 'meter' }, bar);
  const now = el('div', { class: 'muted progress-line' }, '');
  const failure = el('div', {});

  screen(
    'thinking',
    'Setting up.',
    'You can leave this running. If it stops, it will pick up where it left off.',
    [list, meter, now, failure],
  );

  const titles = new Map(plan.steps.map((s) => [s.id, s.title]));
  window.nakaSetup = (event: SetupEvent): void => {
    if (event.kind === 'progress') {
      if (event.total !== null && event.total !== undefined && event.total > 0) {
        bar.style.width = `${Math.min(100, (100 * (event.done ?? 0)) / event.total).toFixed(1)}%`;
        now.textContent = `${event.message ?? ''} — ${((event.done ?? 0) / 1e6).toFixed(0)} of ${(event.total / 1e6).toFixed(0)} MB`;
      } else if ((event.message ?? '') !== '') {
        now.textContent = event.message ?? '';
      }
      return;
    }
    if (event.kind === 'step' && event.step !== undefined) {
      const row = rows.get(event.step);
      if (row)
        paintStep(
          row,
          titles.get(event.step) ?? event.step,
          event.status ?? 'running',
          event.message ?? '',
        );
      if (event.status === 'running') {
        bar.style.width = '0%';
        now.textContent = '';
        meter.hidden = false;
        now.hidden = false;
      }
      if (event.status === 'failed') {
        // The reason takes the progress bar's place, so it is in view.
        meter.hidden = true;
        now.hidden = true;
        showFailure(failure, event.message ?? 'Something went wrong.');
      }
      return;
    }
    if (event.kind === 'done') {
      if (event.ok === true) showDone();
      else orb.setState('sleeping');
    }
  };

  void (fresh ? api.start(choices) : api.start({ ...choices }));
}

const STEP_ICONS: Record<string, IconName> = {
  pending: 'circle',
  running: 'loader',
  ok: 'circle-check',
  skipped: 'circle-check',
  failed: 'circle-alert',
};

function paintStep(
  row: HTMLElement,
  title: string,
  status: string,
  detail: string,
): void {
  row.className = `step ${status}`;
  replace(
    row,
    icon(STEP_ICONS[status] ?? 'circle', 'sm'),
    el('span', { class: 'step-title' }, title),
    el('span', { class: 'muted step-detail' }, status === 'failed' ? '' : detail),
  );
}

function showFailure(host: HTMLElement, reason: string): void {
  const copied = el('span', { class: 'muted' }, '');
  replace(
    host,
    el(
      'div',
      { class: 'warning' },
      icon('circle-alert', 'sm'),
      el('span', { class: 'pre' }, reason),
    ),
    el(
      'div',
      { class: 'row wizard-actions' },
      button('rotate-ccw', 'Try again', () => {
        replace(host);
        orb.setState('thinking');
        void api.retry();
      }),
      button('folder-open', 'Open logs', () => {
        void api.open_logs();
      }),
      button('file-text', 'Copy details', () => {
        void api.diagnostics().then((text) => {
          void navigator.clipboard.writeText(text).then(() => {
            copied.textContent = 'Copied.';
          });
        });
      }),
      copied,
    ),
  );
}

/* 5 — done ----------------------------------------------------------------- */

function showDone(): void {
  const name = choices.identity.assistant || 'Naka';
  screen(
    'speaking',
    `${name} is ready.`,
    `Hold ${keyName(choices.push_to_talk_key)} and talk to her — from any window.`,
    [
      el(
        'p',
        { class: 'muted' },
        `She lives in the tray, by the clock. Right-click the icon to open this window again, ` +
          `release the graphics card before a game, or quit.`,
      ),
    ],
    [
      primary('sparkles', `Start ${name}`, () => {
        void api.finish();
      }),
    ],
  );
}

/* ------------------------------------------------------------------ go */

// pywebview injects its bridge after the page loads; it announces it with an
// event, and a page that asked earlier would find nothing there.
// pywebview defines window.pywebview before its methods are attached, so its
// presence alone is not readiness; the event is.
if (typeof window.pywebview?.api.plan === 'function') begin();
else window.addEventListener('pywebviewready', begin, { once: true });
