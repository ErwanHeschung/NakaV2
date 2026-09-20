/** Small shared pieces: icons, buttons, panels, cards. */

import {
  AudioLines,
  Bell,
  Brain,
  Check,
  ChevronRight,
  CircleAlert,
  Clock,
  Cpu,
  Ellipsis,
  LoaderCircle,
  Mic,
  NotebookPen,
  Pencil,
  Power,
  RefreshCw,
  Settings,
  SlidersHorizontal,
  Trash,
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
  'audio-lines': AudioLines,
  bell: Bell,
  brain: Brain,
  check: Check,
  'chevron-right': ChevronRight,
  'circle-alert': CircleAlert,
  clock: Clock,
  cpu: Cpu,
  ellipsis: Ellipsis,
  loader: LoaderCircle,
  mic: Mic,
  'notebook-pen': NotebookPen,
  pencil: Pencil,
  power: Power,
  'refresh-cw': RefreshCw,
  settings: Settings,
  sliders: SlidersHorizontal,
  trash: Trash,
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
