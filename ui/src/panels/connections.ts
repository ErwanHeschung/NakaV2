/** The Connections drawer: one card per service, then MCP servers. */
import {
  api,
  type ConnectionInfo,
  type ConnectionsState,
  type McpState,
} from '../api.js';
import { mcpSection } from './mcp.js';
import { el, replace } from '../dom.js';
import { ask, button, control, icon, textField, toggle } from '../ui.js';
import {
  reason,
  failed,
  loading,
  rerender,
  busy,
  flash,
  connectionLogo,
} from './common.js';

export function renderConnections(body: HTMLElement): void {
  loading(body);
  void Promise.all([api.connections(), api.mcp()])
    .then(([state, mcp]: [ConnectionsState, McpState]) => {
      busy(body, false);
      replace(
        body,
        el(
          'p',
          { class: 'muted' },
          'Services outside this PC. Each is off until you switch it on, and nothing goes out while it is off. Tokens are kept in Windows Credential Manager, not in the settings file.',
        ),
        ...state.connections.map((connection) => connectionCard(body, connection)),
        mcpSection(body, mcp),
      );
    })
    .catch((error: unknown) => {
      failed(body, error);
    });
}

function statusLine(connection: ConnectionInfo): HTMLElement {
  const { ok, text } = connection.status;
  const kind = ok === true ? 'on' : ok === false ? 'bad' : '';
  return el(
    'div',
    { class: 'conn-status' },
    el('span', { class: `dot ${kind}`.trim() }),
    el('span', {}, text),
  );
}

function connectionCard(body: HTMLElement, connection: ConnectionInfo): HTMLElement {
  const { name } = connection;
  const status = el('span', {});
  const fields = new Map<string, string | number | boolean>();
  const typedSecrets = new Map<string, string>();

  const again = (): void => {
    rerender(body, renderConnections);
  };
  const failedWith = (error: unknown): void => {
    flash(status, reason(error), 'error');
  };
  const edited = (): void => {
    busy(body, fields.size > 0 || typedSecrets.size > 0);
  };

  /** Fields first, then secrets, then a check that it all works. */
  const saveAndCheck = async (): Promise<void> => {
    try {
      if (fields.size > 0) await api.saveSettings(Object.fromEntries(fields));
      for (const [key, value] of typedSecrets) {
        await api.saveSecret(name, key, value);
      }
      fields.clear();
      typedSecrets.clear();
      edited();
      await api.testConnection(name);
      again();
    } catch (error) {
      failedWith(error);
    }
  };

  const toggleOn = toggle(connection.on, (value) => {
    void api
      .saveSettings({ [`settings.connections.${name}.enabled`]: value })
      .then(again)
      .catch(failedWith);
  });

  const inputs = connection.fields.map((field) => {
    const onChange = (value: string | number | boolean): void => {
      if (value === field.value) fields.delete(field.key);
      else fields.set(field.key, value);
      edited();
    };
    if (field.kind === 'bool') {
      return control(
        { label: field.label, help: field.help },
        toggle(field.value === true, onChange),
      );
    }
    return control(
      { label: field.label, help: field.help },
      textField(String(field.value ?? ''), onChange),
    );
  });

  const secretInputs = connection.secrets
    .filter((secret) => secret.typed)
    .map((secret) => {
      const input = el('input', {
        class: 'input',
        type: 'password',
        autocomplete: 'off',
        placeholder: secret.set ? 'saved, paste to replace' : '',
      });
      input.addEventListener('input', () => {
        if (input.value.trim()) typedSecrets.set(secret.key, input.value.trim());
        else typedSecrets.delete(secret.key);
        edited();
      });
      return control({ label: secret.label, help: secret.help }, input);
    });

  const signedIn = connection.secrets.find((secret) => !secret.typed);
  const account = signedIn
    ? el(
        'div',
        { class: 'row' },
        icon(signedIn.set ? 'circle-check' : 'circle', 'sm'),
        el(
          'span',
          { class: signedIn.set ? '' : 'muted' },
          `${signedIn.label}: ${signedIn.set ? 'connected' : 'not connected'}`,
        ),
      )
    : null;

  const link = el('div', {});
  const actions = el(
    'div',
    { class: 'row' },
    button('check', 'Save and check', () => {
      void saveAndCheck();
    }),
    connection.signs_in
      ? button(
          'external-link',
          signedIn?.set === true ? 'Reconnect' : 'Connect',
          () => {
            void api
              .connect(name)
              .then((result) => {
                // The server opens the browser itself. The link is here for
                // when it could not, or opened it somewhere unexpected.
                if (result.url !== null && result.url !== '') {
                  replace(
                    link,
                    el(
                      'p',
                      { class: 'muted' },
                      'Finish in your browser. If nothing opened, ',
                      el('a', { href: result.url, target: '_blank' }, 'open this link'),
                      '.',
                    ),
                  );
                }
              })
              .catch(failedWith);
          },
        )
      : null,
    connection.configured || signedIn?.set === true
      ? button(
          'unplug',
          'Disconnect',
          () => {
            void ask({
              title: `Disconnect ${connection.label}?`,
              body: 'Its saved token is removed from Windows Credential Manager. Connecting again means signing in again.',
              confirm: 'Disconnect',
              danger: true,
              icon: 'unplug',
            }).then((yes) => {
              if (yes) void api.disconnect(name).then(again).catch(failedWith);
            });
          },
          'danger',
        )
      : null,
    status,
  );

  return el(
    'section',
    { class: connection.on ? 'conn' : 'conn off' },
    el(
      'div',
      { class: 'row spaced' },
      el('div', { class: 'row' }, connectionLogo(name), el('h3', {}, connection.label)),
      toggleOn,
    ),
    el('p', { class: 'muted' }, connection.blurb),
    connection.on ? statusLine(connection) : null,
    ...(connection.on
      ? [
          ...inputs,
          ...secretInputs,
          name === 'telegram' ? telegramPairing(connection, again, failedWith) : null,
          account,
          actions,
          link,
          el(
            'details',
            { class: 'guide' },
            el('summary', {}, 'How to set it up'),
            el(
              'ol',
              {},
              ...connection.guide.map((step) =>
                el(
                  'li',
                  {},
                  step.text,
                  step.url
                    ? el(
                        'a',
                        { href: step.url, target: '_blank', class: 'guide-link' },
                        icon('external-link', 'xs'),
                      )
                    : null,
                ),
              ),
            ),
          ),
        ]
      : []),
  );
}

function telegramPairing(
  connection: ConnectionInfo,
  again: () => void,
  failedWith: (error: unknown) => void,
): HTMLElement | null {
  if (connection.owner !== undefined && connection.owner !== 0) {
    return el(
      'div',
      { class: 'row' },
      icon('circle-check', 'sm'),
      el('span', {}, `Paired with chat ${String(connection.owner)}`),
    );
  }
  const candidates = connection.candidates ?? [];
  if (candidates.length === 0) {
    return el(
      'p',
      { class: 'muted' },
      connection.bot !== undefined && connection.bot !== ''
        ? `Send /start to @${connection.bot} from your own Telegram.`
        : 'Once the token is saved, send /start to your bot.',
    );
  }
  return el(
    'div',
    { class: 'list' },
    ...candidates.map((candidate) =>
      el(
        'div',
        { class: 'item row spaced' },
        el(
          'div',
          {},
          el('strong', {}, candidate.name),
          el(
            'div',
            { class: 'muted mono' },
            `${candidate.username ? `@${candidate.username} · ` : ''}chat ${String(candidate.chat_id)}`,
          ),
        ),
        button('check', "That's me", () => {
          void api.pairTelegram(candidate.chat_id).then(again).catch(failedWith);
        }),
      ),
    ),
  );
}
