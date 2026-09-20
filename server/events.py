"""Server to client push, over Server-Sent Events.

Everything else here is request-shaped: the client asks and the server
answers. A timer is the first thing that has to travel the other way — it
comes due whether or not anyone is asking — so it needs a channel that is
already open when it fires.

SSE rather than a WebSocket, because nothing ever travels client to server on
it. EventSource reconnects on its own where a socket needs hand-written
backoff, it is plain HTTP so it can be read with curl and needs no protocol
upgrade, and it costs no dependency — uvicorn cannot serve a WebSocket at all
without `websockets` or `wsproto` installed. The things a socket would buy
here (binary frames, client-to-server traffic, more than six connections per
origin) are all things this does not do.

The hub is deliberately forgetful. It holds no history: a client that is not
connected when a timer goes off is not told later, because a kitchen timer
that rings ten minutes after the pasta is worse than one that never rang.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

log = logging.getLogger("naka.events")

# Long enough to stay quiet, short enough that a browser or any proxy in
# between does not decide the connection died.
KEEPALIVE_SECONDS = 20

# Per client. A panel this far behind is not reading, and unbounded queues
# would grow without limit behind a tab the machine has suspended.
BACKLOG = 32


class Hub:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()

    @property
    def listeners(self) -> int:
        return len(self._clients)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=BACKLOG)
        self._clients.add(queue)
        log.info("client connected (%d listening)", len(self._clients))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._clients.discard(queue)
        log.info("client gone (%d listening)", len(self._clients))

    async def send(self, event: dict) -> None:
        for queue in list(self._clients):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Dropped rather than awaited: one unresponsive tab must not
                # hold up the ring for every other panel that is open.
                log.warning("dropping event for a client that is not reading")


hub = Hub()


def frame(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def stream() -> AsyncIterator[str]:
    """One client's event stream, for a text/event-stream response."""
    queue = hub.subscribe()
    try:
        # Sent immediately so the response headers leave now rather than when
        # the first timer happens to fire, which is what makes EventSource
        # consider the connection open.
        yield ": connected\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(),
                                               timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield frame(event)
    finally:
        # Reached when the client disconnects and the generator is closed.
        hub.unsubscribe(queue)
