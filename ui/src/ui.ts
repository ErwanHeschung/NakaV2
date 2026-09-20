/** Small shared pieces: icons, panels, cards. */

import { el } from './dom.js';
import { icons, type IconName } from './icons.js';

/** Inline an icon. The SVG is trusted — it is compiled in from lucide. */
export function icon(name: IconName, extra = ''): SVGElement {
  const holder = document.createElement('div');
  holder.innerHTML = icons[name];
  const svg = holder.querySelector('svg');
  if (!svg) throw new Error(`icon ${name} has no svg`);
  svg.setAttribute('class', `icon ${extra}`.trim());
  return svg;
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
