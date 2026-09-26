/** Small shared pieces: icons, buttons, panels, cards. */

import {
  ArrowLeft,
  AudioLines,
  Bell,
  BellRing,
  Brain,
  Calendar,
  CloudSun,
  ExternalLink,
  KeyRound,
  MessageCircle,
  Music,
  Plug,
  Unplug,
  Check,
  Circle,
  CircleCheck,
  ChevronRight,
  CircleAlert,
  CirclePlus,
  Clock,
  Cpu,
  Download,
  FileText,
  FolderOpen,
  Ellipsis,
  Globe,
  Keyboard,
  Link,
  LoaderCircle,
  Mic,
  NotebookPen,
  Pencil,
  Plus,
  Power,
  RefreshCw,
  RotateCcw,
  Save,
  SendHorizontal,
  Settings,
  Sparkles,
  SlidersHorizontal,
  Square,
  SquareTerminal,
  Trash,
  TriangleAlert,
  Wrench,
  X,
  Zap,
  createElement,
} from 'lucide';
import { el } from './dom.js';

/**
 * The icons this panel uses, by the names the rest of the code refers to.
 *
 * Lucide's own icon data and createElement() do the work. Adding one here
 * also means adding it to ICONS in scripts/vendor-lucide.mjs, which is what
 * actually copies it into public/vendor.
 */
const ICONS = {
  'arrow-left': ArrowLeft,
  'audio-lines': AudioLines,
  bell: Bell,
  'bell-ring': BellRing,
  brain: Brain,
  calendar: Calendar,
  'cloud-sun': CloudSun,
  'external-link': ExternalLink,
  'key-round': KeyRound,
  'message-circle': MessageCircle,
  music: Music,
  plug: Plug,
  unplug: Unplug,
  check: Check,
  circle: Circle,
  'circle-check': CircleCheck,
  'chevron-right': ChevronRight,
  'circle-alert': CircleAlert,
  'circle-plus': CirclePlus,
  clock: Clock,
  cpu: Cpu,
  download: Download,
  'file-text': FileText,
  'folder-open': FolderOpen,
  ellipsis: Ellipsis,
  globe: Globe,
  keyboard: Keyboard,
  link: Link,
  loader: LoaderCircle,
  mic: Mic,
  'notebook-pen': NotebookPen,
  pencil: Pencil,
  plus: Plus,
  power: Power,
  'refresh-cw': RefreshCw,
  'rotate-ccw': RotateCcw,
  save: Save,
  send: SendHorizontal,
  settings: Settings,
  sparkles: Sparkles,
  sliders: SlidersHorizontal,
  stop: Square,
  terminal: SquareTerminal,
  trash: Trash,
  'triangle-alert': TriangleAlert,
  wrench: Wrench,
  x: X,
  zap: Zap,
} as const;

export type IconName = keyof typeof ICONS;

export function icon(name: IconName, extra = ''): SVGElement {
  // Size and colour come from CSS, so the element inherits from wherever it
  // is placed rather than carrying its own.
  return createElement(ICONS[name], {
    class: `icon ${extra}`.trim(),
    width: '',
    height: '',
  });
}

export function button(
  name: IconName | null,
  label: string,
  onClick: () => void,
  extra = '',
): HTMLButtonElement {
  const node = el('button', { class: `btn ${extra}`.trim(), onclick: onClick });
  if (name) node.append(icon(name, 'sm'));
  node.append(label);
  return node;
}

export function iconButton(
  name: IconName,
  label: string,
  onClick: () => void,
): HTMLButtonElement {
  const node = el('button', {
    class: 'icon-btn',
    title: label,
    'aria-label': label,
    onclick: onClick,
  });
  node.append(icon(name));
  return node;
}

/** A titled surface. Returns the body so callers can render into it. */
export function panel(
  name: IconName,
  title: string,
  extraClass = '',
): { root: HTMLElement; body: HTMLElement; head: HTMLElement } {
  const body = el('div', { class: 'panel-body' });
  const head = el('div', { class: 'panel-head' });
  head.append(icon(name, 'sm'), title);
  const root = el('div', { class: `panel ${extraClass}`.trim() }, head, body);
  return { root, body, head };
}

export function card(
  name: IconName,
  label: string,
  value: string,
  sub = '',
): HTMLElement {
  const labelRow = el('div', { class: 'label' });
  labelRow.append(icon(name, 'sm'), label);
  return el(
    'div',
    { class: 'card' },
    labelRow,
    el('div', { class: 'value' }, value),
    sub ? el('div', { class: 'sub' }, sub) : null,
  );
}

/* ------------------------------------------------------------------ forms */

/**
 * A labelled control.
 *
 * `onChange` is handed the parsed value, never the raw input element, so no
 * caller ends up reading state back out of the DOM — the one rule this
 * framework-free approach depends on to stay coherent.
 */
export function control(
  spec: {
    label: string;
    help?: string;
    badge?: string;
  },
  input: HTMLElement,
): HTMLElement {
  return el(
    'label',
    { class: 'control' },
    el(
      'div',
      { class: 'control-label' },
      spec.label,
      spec.badge === undefined ? null : el('span', { class: 'tag' }, spec.badge),
    ),
    input,
    spec.help === undefined || spec.help === ''
      ? null
      : el('div', { class: 'control-help' }, spec.help),
  );
}

export function textField(
  value: string,
  onChange: (value: string) => void,
  placeholder = '',
): HTMLInputElement {
  const node = el('input', { class: 'input', type: 'text', placeholder });
  node.value = value;
  node.addEventListener('input', () => {
    onChange(node.value);
  });
  return node;
}

export function numberField(
  value: number,
  onChange: (value: number) => void,
  bounds: { min?: number | null; max?: number | null; step?: number | null } = {},
): HTMLInputElement {
  const node = el('input', { class: 'input', type: 'number' });
  if (bounds.min !== null && bounds.min !== undefined) node.min = String(bounds.min);
  if (bounds.max !== null && bounds.max !== undefined) node.max = String(bounds.max);
  node.step = String(bounds.step ?? 1);
  node.value = String(value);
  node.addEventListener('input', () => {
    // An empty or half-typed box is not a value yet; reporting NaN upward
    // would put the form into a state the server is bound to reject.
    if (node.value.trim() === '') return;
    const parsed = Number(node.value);
    if (!Number.isNaN(parsed)) onChange(parsed);
  });
  return node;
}

export function toggle(
  value: boolean,
  onChange: (value: boolean) => void,
): HTMLElement {
  const node = el('input', { class: 'switch', type: 'checkbox' });
  node.checked = value;
  node.addEventListener('change', () => {
    onChange(node.checked);
  });
  return node;
}

export function choiceField(
  value: string,
  options: readonly string[],
  onChange: (value: string) => void,
): HTMLSelectElement {
  const node = el('select', { class: 'input' });
  for (const option of options) {
    const item = el('option', { value: option }, option);
    node.append(item);
  }
  node.value = value;
  node.addEventListener('change', () => {
    onChange(node.value);
  });
  return node;
}

export function textArea(
  value: string,
  onChange: (value: string) => void,
): HTMLTextAreaElement {
  const node = el('textarea', { class: 'input editor', spellcheck: false });
  node.value = value;
  node.addEventListener('input', () => {
    onChange(node.value);
  });
  return node;
}

/**
 * A KeyboardEvent.code as a person would say it.
 *
 * The codes are stored rather than the labels because the code follows the
 * physical key — the one being held — while the character it types moves with
 * the keyboard layout.
 */
export function keyName(code: string): string {
  const named: Record<string, string> = {
    ControlLeft: 'Left Ctrl',
    ControlRight: 'Right Ctrl',
    AltLeft: 'Left Alt',
    AltRight: 'Right Alt',
    ShiftLeft: 'Left Shift',
    ShiftRight: 'Right Shift',
    MetaLeft: 'Left Meta',
    MetaRight: 'Right Meta',
    Space: 'Space',
    CapsLock: 'Caps Lock',
  };
  if (code in named) return named[code] ?? code;
  if (code.startsWith('Key')) return code.slice(3);
  if (code.startsWith('Digit')) return code.slice(5);
  if (code.startsWith('Arrow')) return `${code.slice(5)} arrow`;
  return code;
}

/**
 * Captures a keypress rather than asking for its name.
 *
 * Nobody knows offhand that the right control key is called "ControlRight",
 * and a text box here would mostly collect values the client cannot bind.
 */
export function keyField(
  value: string,
  onChange: (value: string) => void,
): HTMLElement {
  let current = value;
  const node = el('button', { class: 'btn key-field', type: 'button' });

  const paint = (listening: boolean): void => {
    node.classList.toggle('listening', listening);
    node.textContent = '';
    node.append(
      icon(listening ? 'ellipsis' : 'keyboard', 'sm'),
      listening ? 'press any key…' : keyName(current),
    );
  };

  const capture = (event: KeyboardEvent): void => {
    event.preventDefault();
    event.stopPropagation();
    globalThis.removeEventListener('keydown', capture, true);
    // Escape leaves it as it was, which is the only way out otherwise: every
    // other key, including Tab, is a legitimate choice here.
    if (event.code !== 'Escape') {
      current = event.code;
      onChange(event.code);
    }
    paint(false);
  };

  node.addEventListener('click', () => {
    paint(true);
    // Capture phase: this has to win against the push-to-talk binding, which
    // is listening for one of the very keys being offered.
    globalThis.addEventListener('keydown', capture, true);
  });

  paint(false);
  return node;
}

/* ---------------------------------------------------------------- dialogs */

export interface AskSpec {
  title: string;
  /** What will happen, in a sentence or two. */
  body?: string;
  /** The button that goes ahead: "Delete", "Disconnect". Never just "OK". */
  confirm: string;
  /** Shown in red, and the safe choice gets the focus. */
  danger?: boolean;
  /** On the confirming button; a bin for danger and a tick otherwise. */
  icon?: IconName;
}

/**
 * Ask before doing something, in the panel's own look.
 *
 * Replaces window.confirm, which in the tray's WebView2 window shows a bare
 * system box titled with the page's address, freezes the page's script while
 * it is open, and cannot say which button destroys something.
 *
 * Built on <dialog> with showModal(): the browser supplies the backdrop, the
 * focus trap and Escape, so none of it is hand-written. Resolves true only
 * when the confirming button is pressed; Escape, Cancel and a click on the
 * backdrop all resolve false.
 */
export function ask(spec: AskSpec): Promise<boolean> {
  return new Promise((resolve) => {
    const dialog = el('dialog', { class: 'ask' });
    const close = (answer: boolean): void => {
      dialog.close(answer ? 'yes' : 'no');
    };

    const cancel = button(null, 'Cancel', () => {
      close(false);
    });
    const go = button(
      spec.icon ?? (spec.danger === true ? 'trash' : 'check'),
      spec.confirm,
      () => {
        close(true);
      },
      spec.danger === true ? 'danger solid' : 'primary',
    );

    dialog.append(el('h2', {}, spec.title));
    if (spec.body !== undefined) dialog.append(el('p', {}, spec.body));
    dialog.append(el('div', { class: 'ask-actions' }, cancel, go));

    // A click whose target is the dialog itself landed on the backdrop: the
    // content fills the box, so anything inside targets a child.
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) close(false);
    });
    dialog.addEventListener(
      'close',
      () => {
        resolve(dialog.returnValue === 'yes');
        dialog.remove();
      },
      { once: true },
    );

    document.body.append(dialog);
    dialog.showModal();
    // Enter on a destructive dialog should not destroy anything.
    (spec.danger === true ? cancel : go).focus();
  });
}
