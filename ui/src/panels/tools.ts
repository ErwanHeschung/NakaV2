/** The Tools drawer: what she can use, grouped by the switch it needs. */
import { api, type ToolInfo, type ToolsState } from '../api.js';
import { el, replace } from '../dom.js';
import { icon, toggle } from '../ui.js';
import { failed, loading, rerender } from './common.js';

const TOOL_GROUPS: {
  power: 'web' | 'shell' | null;
  title: string;
  off: string;
}[] = [
  { power: null, title: 'Everyday', off: '' },
  {
    power: 'web',
    title: 'Web',
    off: 'Off. She answers from what she already knows.',
  },
  {
    power: 'shell',
    title: 'PowerShell',
    off: 'Off. She cannot run anything on this PC.',
  },
];

const CONFIRM_TAG: Record<ToolInfo['confirms'], string | null> = {
  always: 'asks first',
  changes: 'asks before changes',
  never: null,
};

function toolItem(tool: ToolInfo): HTMLElement {
  const tag = CONFIRM_TAG[tool.confirms];
  return el(
    'div',
    { class: tool.enabled ? 'item' : 'item off', title: tool.name },
    el(
      'div',
      { class: 'row' },
      el('strong', {}, tool.label),
      tag === null ? null : el('span', { class: 'tag warn' }, tag),
    ),
    el('div', { class: 'muted' }, tool.summary),
  );
}

export function renderTools(body: HTMLElement): void {
  loading(body);
  void api
    .tools()
    .then((state: ToolsState) => {
      const offered = state.allowed.filter((t) => t.enabled).length;
      const waiting = state.awaiting_confirmation;
      const label = (name: string): string =>
        state.allowed.find((t) => t.name === name)?.label ?? name;

      const groups = TOOL_GROUPS.map(({ power, title, off }) => {
        const tools = state.allowed.filter((t) => t.power === power);
        if (tools.length === 0) return null;
        const on = power === null || state.powers[power];
        // The switch lives here as well as in Settings: this is where the
        // missing tools are noticed, so this is where they get turned on.
        const heading = power
          ? el(
              'div',
              { class: 'row spaced' },
              el('h3', {}, title),
              toggle(on, (value) => {
                void api
                  .saveSettings({ [`settings.powers.${power}`]: value })
                  .then(() => {
                    rerender(body, renderTools);
                  })
                  .catch((error: unknown) => {
                    failed(body, error);
                  });
              }),
            )
          : el('h3', {}, title);
        return el(
          'section',
          {},
          heading,
          on ? null : el('p', { class: 'muted' }, off),
          el('div', { class: 'list' }, ...tools.map((t) => toolItem(t))),
        );
      });

      // One group per connection, after the powers. Their switches live on
      // the Connections cards, where the setup that goes with them is.
      const linked = Object.entries(state.connections).map(([name, conn]) => {
        const tools = state.allowed.filter((t) => t.power === `conn.${name}`);
        if (tools.length === 0) return null;
        return el(
          'section',
          {},
          el(
            'div',
            { class: 'row spaced' },
            el('h3', {}, conn.label),
            el(
              'a',
              { class: 'muted', href: '#connections' },
              conn.ready ? 'connected' : conn.on ? 'not set up' : 'off',
            ),
          ),
          el('div', { class: 'list' }, ...tools.map((t) => toolItem(t))),
        );
      });

      // And one per running MCP server, named by the power its tools carry.
      const servers = [
        ...new Set(
          state.allowed
            .map((t) => t.power)
            .filter((p): p is `mcp.${string}` => p?.startsWith('mcp.') === true),
        ),
      ];
      const plugged = servers.map((power) =>
        el(
          'section',
          {},
          el(
            'div',
            { class: 'row spaced' },
            el('h3', {}, `MCP: ${power.slice(4)}`),
            el('a', { class: 'muted', href: '#connections' }, 'server'),
          ),
          el(
            'div',
            { class: 'list' },
            ...state.allowed.filter((t) => t.power === power).map((t) => toolItem(t)),
          ),
        ),
      );

      replace(
        body,
        el(
          'p',
          { class: 'muted' },
          `${offered} offered · up to ${state.max_steps} steps · ${state.timeout_s}s per request`,
        ),
        waiting
          ? el(
              'div',
              { class: 'warning' },
              icon('triangle-alert', 'sm'),
              el(
                'span',
                {},
                `Waiting for your yes: ${label(waiting.name)}` +
                  (typeof waiting.arguments.command === 'string'
                    ? ` — ${waiting.arguments.command}`
                    : ''),
              ),
            )
          : null,
        ...groups,
        ...linked,
        ...plugged,
      );
    })
    .catch((error: unknown) => {
      failed(body, error);
    });
}
