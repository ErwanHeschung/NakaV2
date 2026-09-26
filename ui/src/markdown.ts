/**
 * Her replies, rendered.
 *
 * A small Markdown renderer for what a chat reply actually contains:
 * paragraphs, headings, lists (nested by indentation), quotes, rules,
 * tables, fenced code with a copy button, and inline code, bold, italics,
 * strikethrough and links.
 *
 * It builds DOM nodes and never assigns HTML, so nothing she writes (or a
 * web page she quotes) can inject markup or script: a <script> in a reply
 * is shown as the text "<script>". Links go out only to http and https.
 *
 * Replies arrive a sentence at a time and the whole text is rendered again
 * on each one. A code block still being written has no closing fence yet;
 * it is shown as code up to where it has got, not as stray backticks.
 */

import hljs from 'hljs';

import { el } from './dom.js';
import { icon } from './ui.js';

type Inline = (Node | string)[];

const FENCE = /^[ \t]*(```|~~~)[ \t]*([\w+#.-]*)[ \t]*$/u;
const HEADING = /^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$/u;
const RULE = /^[ \t]*(?:[-*_][ \t]*){3,}$/u;
const QUOTE = /^[ \t]*>[ \t]?(.*)$/u;
const BULLET = /^([ \t]*)([-*+•]|\d{1,3}[.)])[ \t]+(.*)$/u;
const HEADING_TAGS = ['h3', 'h4', 'h5', 'h6'] as const;
const TABLE_SEPARATOR =
  /^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$/u;

/** A reply's Markdown as nodes, ready to put in a bubble. */
export function markdown(text: string): Node[] {
  return blocks(text.replaceAll('\r\n', '\n').split('\n'));
}

function blocks(lines: string[]): Node[] {
  const out: Node[] = [];
  let paragraph: string[] = [];

  const endParagraph = (): void => {
    if (paragraph.length === 0) return;
    const content: Inline = [];
    for (const [index, line] of paragraph.entries()) {
      if (index > 0) content.push(el('br'));
      content.push(...inline(line.trim()));
    }
    out.push(el('p', {}, ...content));
    paragraph = [];
  };

  let at = 0;
  while (at < lines.length) {
    const line = lines[at] ?? '';

    const fence = FENCE.exec(line);
    if (fence) {
      endParagraph();
      const marker = fence[1] ?? '```';
      const body: string[] = [];
      at += 1;
      while (at < lines.length && !(lines[at] ?? '').trim().startsWith(marker)) {
        body.push(lines[at] ?? '');
        at += 1;
      }
      // Past the closing fence, if it has arrived.
      at += 1;
      out.push(codeBlock(body.join('\n'), fence[2] ?? ''));
      continue;
    }

    if (line.trim() === '') {
      endParagraph();
      at += 1;
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      endParagraph();
      // Two levels down: a bubble's heading must not outrank the panel's.
      const depth = (heading[1] ?? '#').length;
      const tag = HEADING_TAGS[Math.min(depth, HEADING_TAGS.length) - 1] ?? 'h6';
      out.push(el(tag, { class: 'md-heading' }, ...inline(heading[2] ?? '')));
      at += 1;
      continue;
    }

    if (RULE.test(line)) {
      endParagraph();
      out.push(el('hr'));
      at += 1;
      continue;
    }

    if (QUOTE.test(line)) {
      endParagraph();
      const quoted: string[] = [];
      while (at < lines.length) {
        const match = QUOTE.exec(lines[at] ?? '');
        if (!match) break;
        quoted.push(match[1] ?? '');
        at += 1;
      }
      out.push(el('blockquote', {}, ...blocks(quoted)));
      continue;
    }

    if (line.includes('|') && TABLE_SEPARATOR.test(lines[at + 1] ?? '')) {
      endParagraph();
      const rows: string[] = [line];
      at += 2;
      while (at < lines.length && (lines[at] ?? '').includes('|')) {
        rows.push(lines[at] ?? '');
        at += 1;
      }
      out.push(table(rows));
      continue;
    }

    if (BULLET.test(line)) {
      endParagraph();
      const start = at;
      const base = indentOf(line);
      const ordered = isOrdered(line);
      // At the list's own depth, only an item of the same kind continues
      // it: "1." after a run of "-" starts a new, numbered list.
      const sameList = (candidate: string): boolean =>
        BULLET.test(candidate) &&
        (indentOf(candidate) > base || isOrdered(candidate) === ordered);
      at += 1;
      // The list runs on through its items, their indented continuations,
      // and single blank lines between items.
      while (at < lines.length) {
        const next = lines[at] ?? '';
        if (sameList(next) || (!BULLET.test(next) && /^[ \t]{2,}\S/u.test(next))) {
          at += 1;
        } else if (next.trim() === '' && sameList(lines[at + 1] ?? '')) {
          at += 1;
        } else {
          break;
        }
      }
      out.push(list(lines.slice(start, at)));
      continue;
    }

    paragraph.push(line);
    at += 1;
  }
  endParagraph();
  return out;
}

function isOrdered(line: string): boolean {
  return /\d/u.test(BULLET.exec(line)?.[2] ?? '');
}

function indentOf(line: string): number {
  return (/^[ \t]*/u.exec(line)?.[0] ?? '').replaceAll('\t', '    ').length;
}

function list(lines: string[]): HTMLElement {
  const first = BULLET.exec(lines[0] ?? '');
  const base = indentOf(lines[0] ?? '');
  const ordered = /\d/u.test(first?.[2] ?? '');
  const items: { text: string; children: string[] }[] = [];

  for (const line of lines) {
    const match = BULLET.exec(line);
    if (match && indentOf(line) <= base) {
      items.push({ text: match[3] ?? '', children: [] });
    } else if (line.trim() !== '') {
      items.at(-1)?.children.push(line);
    }
  }

  const node = el(ordered ? 'ol' : 'ul', {});
  if (ordered) {
    const startsAt = Math.trunc(Number(/\d+/u.exec(first?.[2] ?? '1')?.[0] ?? '1'));
    if (startsAt !== 1) node.setAttribute('start', String(startsAt));
  }
  for (const item of items) {
    const li = el('li', {}, ...inline(item.text));
    if (item.children.length > 0) {
      const nested = item.children.some((line) => BULLET.test(line));
      if (nested) li.append(...blocks(item.children));
      else
        li.append(' ', ...inline(item.children.map((line) => line.trim()).join(' ')));
    }
    node.append(li);
  }
  return node;
}

function cells(row: string): string[] {
  return row
    .trim()
    .replace(/^\|/u, '')
    .replace(/\|$/u, '')
    .split('|')
    .map((cell) => cell.trim());
}

function table(rows: string[]): HTMLElement {
  const [head = '', ...body] = rows;
  return el(
    'div',
    { class: 'md-table' },
    el(
      'table',
      {},
      el(
        'thead',
        {},
        el('tr', {}, ...cells(head).map((c) => el('th', {}, ...inline(c)))),
      ),
      el(
        'tbody',
        {},
        ...body.map((row) =>
          el('tr', {}, ...cells(row).map((c) => el('td', {}, ...inline(c)))),
        ),
      ),
    ),
  );
}

function codeBlock(code: string, language: string): HTMLElement {
  const copy = el('button', { class: 'md-copy', title: 'Copy', type: 'button' });
  copy.append(icon('copy', 'xs'), 'Copy');
  copy.addEventListener('click', () => {
    void navigator.clipboard.writeText(code).then(
      () => {
        copy.replaceChildren(icon('check', 'xs'), 'Copied');
        setTimeout(() => {
          copy.replaceChildren(icon('copy', 'xs'), 'Copy');
        }, 1500);
      },
      () => {
        copy.replaceChildren('Copy failed');
      },
    );
  });
  return el(
    'div',
    { class: 'md-code' },
    el('div', { class: 'md-code-head' }, el('span', {}, language || 'code'), copy),
    el('pre', {}, highlighted(code, language)),
  );
}

/**
 * The code, coloured by highlight.js when it knows the language.
 *
 * highlight.js escapes the code itself and adds only its own <span> tags,
 * which is the one place this renderer lets HTML in. Without a known
 * language the code is shown plain: guessing coloured shell commands as
 * SQL often enough to be worse than no colour.
 */
function highlighted(code: string, language: string): HTMLElement {
  const node = el('code', { class: 'hljs' });
  const name = language.toLowerCase();
  if (name && hljs.getLanguage(name) !== undefined) {
    // oxlint-disable-next-line no-unsanitized/property -- escaped by highlight.js
    node.innerHTML = hljs.highlight(code, {
      language: name,
      ignoreIllegals: true,
    }).value;
  } else {
    node.textContent = code;
  }
  return node;
}

/* ----------------------------------------------------------------- inline */

// Order matters: code first, so nothing inside backticks is read as markup.
const INLINE =
  /`([^`\n]+)`|\[([^\]\n]+)\]\(([^)\s]+)\)|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])|\*\*(.+?)\*\*|__(.+?)__|~~(.+?)~~|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])/gu;

function link(href: string, label: Inline): HTMLElement {
  return el('a', { href, target: '_blank', rel: 'noreferrer noopener' }, ...label);
}

export function inline(text: string): Inline {
  const out: Inline = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index;
    if (at > last) out.push(text.slice(last, at));
    const [whole, code, label, href, bare, strong, strong2, strike, em, em2] = match;
    if (code !== undefined) out.push(el('code', {}, code));
    else if (label !== undefined && href !== undefined) {
      // Only the web is linked. A javascript: or file: link is shown as
      // the text it was written as, and cannot be clicked.
      out.push(/^https?:\/\//iu.test(href) ? link(href, inline(label)) : whole);
    } else if (bare !== undefined) out.push(link(bare, [bare]));
    else if (strong !== undefined || strong2 !== undefined) {
      out.push(el('strong', {}, ...inline(strong ?? strong2 ?? '')));
    } else if (strike !== undefined) out.push(el('s', {}, ...inline(strike)));
    else if (em !== undefined || em2 !== undefined) {
      out.push(el('em', {}, ...inline(em ?? em2 ?? '')));
    } else out.push(whole);
    last = at + whole.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/**
 * Put two parts of a reply together the way the server does
 * (server/speech.py join): a space only where they would run into each other.
 */
export function joinReply(before: string, part: string): string {
  if (!before) return part;
  if (!part) return before;
  const gap = /\s$/u.test(before) || /^\s/u.test(part) ? '' : ' ';
  return `${before}${gap}${part}`;
}
