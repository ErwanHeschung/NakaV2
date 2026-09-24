/**
 * The server's side of the conversation, for things that are not answers.
 *
 * The panel does not poll for changes; it is told. Every section names the
 * topic it displays and is re-rendered when that topic fires, which makes
 * being current the default rather than something each panel has to remember
 * to arrange. A section that forgets to poll is stale forever; a section that
 * forgets to name a topic simply does not refresh, which is visible the first
 * time anyone looks at it.
 *
 * EventSource rather than a WebSocket: nothing travels the other way, and it
 * reconnects by itself after a server restart or a sleeping laptop, which is
 * most of what a hand-written socket client ends up being.
 */

export type Topic =
  | 'notes'
  | 'timers'
  | 'memory'
  | 'conversation'
  | 'settings'
  | 'client'
  | 'turn'
  | 'ops';

export interface ServerEvent {
  topic: Topic;
  /** Only on a timer that has come due. */
  rang?: string;
  at?: number;
  /** Only on 'client': what the tray's push-to-talk is doing. */
  state?: string;
  /** Only on 'client': the tray asking its window to come forward. */
  show?: boolean;
  /** Only on 'client': a stop was asked for, so any playback should end. */
  interrupt?: boolean;
  /** Only on 'turn': which exchange, and what just happened in it. */
  id?: number;
  phase?: 'heard' | 'sentence' | 'done';
  /** On 'heard', what was said to her; on 'sentence', what she said. */
  text?: string;
  via?: 'voice' | 'text';
  interrupted?: boolean;
}

interface Handlers {
  onEvent: (event: ServerEvent) => void;
  /** Connected or not, for the status line. */
  onConnection?: (live: boolean) => void;
}

export function listen(handlers: Handlers): () => void {
  let source: EventSource | null = null;
  let stopped = false;

  const open = (): void => {
    if (stopped) return;
    source = new EventSource('/events');

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
      if (typeof parsed.topic === 'string') handlers.onEvent(parsed);
    });
  };

  // Opened once the page has loaded rather than during module evaluation.
  //
  // Only a small thing: browsers allow six connections per origin over
  // HTTP/1.1, and this one is held for the life of the tab, so there is no
  // reason for it to compete with the module graph and the stylesheet while
  // those are still arriving. It does not block the load event — that was
  // measured, after a page that would not load turned out to have nothing to
  // do with this and everything to do with WSL2 forwarding.
  if (document.readyState === 'complete') open();
  else globalThis.addEventListener('load', open, { once: true });

  return () => {
    stopped = true;
    source?.close();
  };
}
