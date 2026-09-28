/** MCP servers, the last part of the Connections drawer. */
import { api, type McpServer, type McpServerIn, type McpState } from '../api.js';
import { el } from '../dom.js';
import {
  ask,
  button,
  choiceField,
  control,
  textArea,
  textField,
  toggle,
} from '../ui.js';
import { reason, rerender, busy, flash, connectionLogo } from './common.js';
import { renderConnections } from './connections.js';

const MCP_STATUS: Record<McpServer['status'], { dot: string; text: string }> = {
  running: { dot: 'on', text: 'running' },
  starting: { dot: '', text: 'starting…' },
  stopped: { dot: '', text: 'off' },
  failed: { dot: 'bad', text: 'failed' },
};

export function mcpSection(body: HTMLElement, state: McpState): HTMLElement {
  const again = (): void => {
    rerender(body, renderConnections);
  };
  return el(
    'section',
    { class: 'conn' },
    el(
      'div',
      { class: 'row spaced' },
      el('div', { class: 'row' }, connectionLogo('mcp'), el('h3', {}, 'MCP servers')),
    ),
    el(
      'p',
      { class: 'muted' },
      'Tools from other programs, through the Model Context Protocol. A server is a program Naka starts on this PC; add only ones you trust. Its tools ask before anything that is not read-only, and what they return is treated like a web page.',
    ),
    ...state.servers.map((server) => mcpCard(server, again)),
    state.servers.length === 0 ? el('p', { class: 'muted' }, 'None yet.') : null,
    mcpAdder(body, again),
    el('div', { class: 'muted mono ellipsis' }, state.file),
  );
}

function mcpCard(server: McpServer, again: () => void): HTMLElement {
  const status = el('span', {});
  const failedWith = (error: unknown): void => {
    flash(status, reason(error), 'error');
  };
  const shown = MCP_STATUS[server.status];
  const asking = server.tools.filter((tool) => tool.asks).length;
  const save = (changes: Partial<McpServerIn>): void => {
    void api
      .saveMcp(server.name, {
        command: server.command,
        args: server.args,
        env: {},
        confirm: server.confirm,
        disabled: !server.enabled,
        ...changes,
      })
      .then(again)
      .catch(failedWith);
  };
  return el(
    'div',
    { class: 'item mcp' },
    el(
      'div',
      { class: 'row spaced' },
      el('strong', {}, server.name),
      toggle(server.enabled, (value) => {
        save({ disabled: !value });
      }),
    ),
    el(
      'div',
      { class: 'conn-status' },
      el('span', { class: `dot ${shown.dot}`.trim() }),
      el(
        'span',
        {},
        server.status === 'running'
          ? `${String(server.tools.length)} tools · ${String(asking)} ask first`
          : server.error || shown.text,
      ),
    ),
    el(
      'div',
      {
        class: 'muted mono ellipsis',
        title: [server.command, ...server.args].join(' '),
      },
      [server.command, ...server.args].join(' '),
    ),
    server.tools.length > 0
      ? el(
          'details',
          { class: 'guide' },
          el('summary', {}, 'Tools'),
          el(
            'ul',
            { class: 'mcp-tools' },
            ...server.tools.map((tool) =>
              el(
                'li',
                {},
                el('span', { class: 'mono' }, tool.name),
                tool.asks ? el('span', { class: 'tag warn' }, 'asks first') : null,
                tool.description
                  ? el('div', { class: 'muted' }, tool.description)
                  : null,
              ),
            ),
          ),
        )
      : null,
    el(
      'div',
      { class: 'row' },
      control(
        { label: 'Ask before' },
        choiceField(server.confirm, ['writes', 'always', 'never'], (value) => {
          if (value === 'writes' || value === 'always' || value === 'never') {
            save({ confirm: value });
          }
        }),
      ),
    ),
    el(
      'div',
      { class: 'row' },
      button('refresh-cw', 'Restart', () => {
        void api.restartMcp(server.name).then(again).catch(failedWith);
      }),
      button(
        'trash',
        'Remove',
        () => {
          void ask({
            title: `Remove ${server.name}?`,
            body: 'The server is stopped and taken out of mcp.json, and its saved environment values are deleted.',
            confirm: 'Remove',
            danger: true,
          }).then((yes) => {
            if (yes) void api.deleteMcp(server.name).then(again).catch(failedWith);
          });
        },
        'danger',
      ),
      status,
    ),
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** One server from pasted JSON: Claude Desktop's shape, or just its body. */
function fromPasted(text: string): { name: string; spec: McpServerIn } | string {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return 'That is not valid JSON.';
  }
  if (!isRecord(data)) return 'Expected a JSON object.';
  const servers = isRecord(data.mcpServers)
    ? data.mcpServers
    : isRecord(data.servers)
      ? data.servers
      : data;
  for (const [name, spec] of Object.entries(servers)) {
    if (!isRecord(spec) || typeof spec.command !== 'string') continue;
    const env: Record<string, string> = {};
    if (isRecord(spec.env)) {
      for (const [key, value] of Object.entries(spec.env)) env[key] = String(value);
    }
    return {
      name,
      spec: {
        command: spec.command,
        args: Array.isArray(spec.args) ? spec.args.map(String) : [],
        env,
        confirm: 'writes',
        disabled: false,
      },
    };
  }
  return 'No server with a "command" in it.';
}

function mcpAdder(body: HTMLElement, again: () => void): HTMLElement {
  const status = el('span', {});
  let name = '';
  let command = '';
  let args = '';
  let env = '';
  let pasted = '';

  const add = (serverName: string, spec: McpServerIn): void => {
    const line = [spec.command, ...spec.args].join(' ');
    void ask({
      title: `Add ${serverName}?`,
      body: `Naka will start this program on your PC now and each time it starts: ${line}`,
      confirm: 'Add and start',
    }).then((yes) => {
      if (!yes) return;
      busy(body, false);
      void api
        .saveMcp(serverName, spec)
        .then(again)
        .catch((error: unknown) => {
          flash(status, reason(error), 'error');
        });
    });
  };

  const fromFields = (): void => {
    const vars: Record<string, string> = {};
    for (const line of env.split('\n')) {
      const at = line.indexOf('=');
      if (at > 0) vars[line.slice(0, at).trim()] = line.slice(at + 1).trim();
    }
    if (!name.trim() || !command.trim()) {
      flash(status, 'A server needs a name and a command.', 'error');
      return;
    }
    add(name.trim(), {
      command: command.trim(),
      args: args
        .split('\n')
        .map((arg) => arg.trim())
        .filter(Boolean),
      env: vars,
      confirm: 'writes',
      disabled: false,
    });
  };

  const typed = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    busy(
      body,
      [name, command, args, env, pasted].some((v) => v.trim() !== ''),
    );
  };

  return el(
    'details',
    { class: 'guide mcp-add' },
    el('summary', {}, 'Add a server'),
    control(
      {
        label: 'Paste its configuration',
        help: 'The JSON from the server’s instructions, as written for Claude Desktop ("mcpServers").',
      },
      textArea(
        '',
        typed((value) => {
          pasted = value;
        }),
      ),
    ),
    el(
      'div',
      { class: 'row' },
      button('plus', 'Add from JSON', () => {
        const parsed = fromPasted(pasted);
        if (typeof parsed === 'string') flash(status, parsed, 'error');
        else add(parsed.name, parsed.spec);
      }),
    ),
    el('p', { class: 'muted' }, 'Or fill it in:'),
    control(
      { label: 'Name' },
      textField(
        '',
        typed((value) => {
          name = value;
        }),
        'filesystem',
      ),
    ),
    control(
      { label: 'Command' },
      textField(
        '',
        typed((value) => {
          command = value;
        }),
        'npx',
      ),
    ),
    control(
      { label: 'Arguments', help: 'One per line.' },
      textArea(
        '',
        typed((value) => {
          args = value;
        }),
      ),
    ),
    control(
      {
        label: 'Environment',
        help: 'KEY=value, one per line. Stored in Windows Credential Manager.',
      },
      textArea(
        '',
        typed((value) => {
          env = value;
        }),
      ),
    ),
    el('div', { class: 'row' }, button('plus', 'Add', fromFields), status),
  );
}
