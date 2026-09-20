import { mountMemory } from './panels/memory.js';
import { mountStatus } from './panels/status.js';

function host(id: string): HTMLElement {
  const node = document.querySelector<HTMLElement>(`#${id}`);
  if (!node) throw new Error(`missing #${id} in the page`);
  return node;
}

mountStatus(host('status'));
mountMemory(host('memory'));
