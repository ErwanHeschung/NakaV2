/**
 * The server's side of the conversation, for things that are not answers.
 *
 * EventSource rather than a WebSocket: nothing travels the other way, and it
 * reconnects by itself after a server restart or a sleeping laptop, which is
 * most of what a hand-written socket client ends up being.
 */

export interface TimerRang {
  type: 'timer';
  label: string;
  at: number;
}

type ServerEvent = TimerRang;

interface Handlers {
  onTimer: (label: string) => void;
  /** Connected or not, for the status line. */
  onConnection?: (live: boolean) => void;
}

export function listen(handlers: Handlers): () => void {
  const source = new EventSource('/events');

  source.addEventListener('open', () => {
    handlers.onConnection?.(true);
  });

  source.addEventListener('error', () => {
    // Not fatal and not worth reporting loudly: EventSource is already
    // retrying, and a server restart takes this path every time.
    handlers.onConnection?.(false);
  });

  source.addEventListener('message', (event: MessageEvent<string>) => {
    let parsed: ServerEvent;
    try {
      // oxlint-disable-next-line typescript/no-unsafe-type-assertion
      parsed = JSON.parse(event.data) as ServerEvent;
    } catch {
      return;
    }
    if (parsed.type === 'timer') handlers.onTimer(parsed.label);
  });

  return () => {
    source.close();
  };
}
