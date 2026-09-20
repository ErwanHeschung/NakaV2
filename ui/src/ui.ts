/** Small shared pieces: icons, buttons, panels, cards. */

import {
  ArrowLeft,
  AudioLines,
  Bell,
  Brain,
  Check,
  ChevronRight,
  CircleAlert,
  CirclePlus,
  Clock,
  Cpu,
  FileText,
  Ellipsis,
  LoaderCircle,
  Mic,
  NotebookPen,
  Pencil,
  Plus,
  Power,
  RefreshCw,
  RotateCcw,
  Save,
  Settings,
  SlidersHorizontal,
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
  brain: Brain,
  check: Check,
  'chevron-right': ChevronRight,
  'circle-alert': CircleAlert,
  'circle-plus': CirclePlus,
  clock: Clock,
  cpu: Cpu,
  'file-text': FileText,
  ellipsis: Ellipsis,
  loader: LoaderCircle,
  mic: Mic,
  'notebook-pen': NotebookPen,
  pencil: Pencil,
  plus: Plus,
  power: Power,
  'refresh-cw': RefreshCw,
  'rotate-ccw': RotateCcw,
  save: Save,
  settings: Settings,
  sliders: SlidersHorizontal,
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
