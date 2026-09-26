"""MCP servers: other people's tools, plugged in without touching Naka's code.

A server is a program started by Naka that speaks the Model Context Protocol
over its stdin and stdout: JSON-RPC 2.0, one message per line. Naka asks it
for its tools once it has started, and offers them to the model as
mcp_<server>_<tool>, through the same registry as her own, so the same
guardrails hold:

  * a server is off until switched on in the panel, and its tools exist
    only while it runs;
  * a tool that does not declare itself read-only asks for a yes before it
    runs, and every tool asks once the turn has been steered from outside;
  * what a tool returns was written by someone else's program, so it taints
    the rest of the turn like a web page;
  * every call is audited.

The configuration is mcp.json in the config folder, in the same shape as
Claude Desktop's ("mcpServers": {name: {command, args, env}}), so a server's
own instructions can be pasted in unchanged. Only stdio servers: they are
nearly all of them, and they need nothing listening on the network.

Hand-written rather than the official SDK: the three messages used here
(initialize, tools/list, tools/call) do not justify the SDK's twenty-nine
dependencies inside the runtime.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import Future
from pathlib import Path

from . import events, paths, winjob
from .tools import registry

log = logging.getLogger("naka.mcp")

CONFIG = paths.CONFIG / "mcp.json"
# Written in mcp.json in place of an environment value the panel stored in
# Credential Manager: API keys do not belong in a JSON file.
IN_VAULT = "<in Windows Credential Manager>"
PROTOCOL = "2025-06-18"
START_SECONDS = 60.0  # npx may download the server on first use
CALL_SECONDS = 60.0
RESULT_CHARS = 4000
_NO_WINDOW = 0x08000000


def load() -> dict[str, dict]:
    """The servers in mcp.json, by name. A broken file is logged, not fatal."""
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        log.error("%s is not valid JSON (%s); no MCP servers", CONFIG, e)
        return {}
    servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
    return {name: spec for name, spec in servers.items()
            if isinstance(spec, dict) and spec.get("command")}


def save(servers: dict[str, dict]) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"mcpServers": servers}, indent=2,
                              ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(CONFIG)


def resolve_env(name: str, env: dict) -> dict[str, str]:
    """The environment as written, with vaulted values fetched back."""
    from .connections import secrets

    stored = {}
    if any(v == IN_VAULT for v in env.values()):
        try:
            stored = json.loads(secrets.get("mcp", f"{name}.env") or "{}")
        except json.JSONDecodeError:
            stored = {}
    return {k: str(stored.get(k, "") if v == IN_VAULT else v)
            for k, v in env.items()}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


class Server:
    def __init__(self, name: str, spec: dict) -> None:
        self.name = name
        self.spec = spec
        self.process: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self.status = "stopped"
        self.error = ""
        self.stderr: deque[str] = deque(maxlen=40)
        self._next = 0
        self._waiting: dict[int, Future] = {}
        self._lock = threading.Lock()
        self._write = threading.Lock()

    # ------------------------------------------------------------ process

    def start(self) -> None:
        command = str(self.spec["command"])
        # npx, uvx and friends are .cmd files on Windows: found by which(),
        # with PATHEXT, where Popen alone would not look.
        found = shutil.which(command) or command
        args = [str(a) for a in self.spec.get("args", [])]
        env = {**os.environ, **resolve_env(self.name, self.spec.get("env") or {})}
        self.status, self.error = "starting", ""
        events.publish("connections")
        try:
            self.process = subprocess.Popen(
                [found, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, cwd=str(Path.home()),
                creationflags=_NO_WINDOW)
        except OSError as e:
            self._fail(f"could not start {command!r}: {e}")
            return
        # Dies with Naka, like llama-server: a server left running after
        # Naka quits would be a program nobody knows is there.
        winjob.contain(self.process.pid)
        threading.Thread(target=self._read, daemon=True,
                         name=f"mcp-{self.name}").start()
        threading.Thread(target=self._drain, daemon=True,
                         name=f"mcp-{self.name}-err").start()
        try:
            self.request("initialize", {
                "protocolVersion": PROTOCOL, "capabilities": {},
                "clientInfo": {"name": "Naka", "version": "0.1.0"}},
                timeout=START_SECONDS)
            self.notify("notifications/initialized")
            self.tools = self._list_tools()
        except Exception as e:
            self._fail(str(e) or type(e).__name__)
            self.stop()
            return
        self.status = "running"
        self._register()
        log.info("MCP server %s: %d tools", self.name, len(self.tools))
        events.publish("connections")

    def stop(self) -> None:
        registry.unregister(f"mcp_{slug(self.name)}_")
        process, self.process = self.process, None
        if process and process.poll() is None:
            try:
                process.stdin.close()
                process.wait(timeout=3)
            except Exception:
                process.kill()
        self._abandon("the server stopped")
        if self.status != "failed":
            self.status = "stopped"
        events.publish("connections")

    def _fail(self, why: str) -> None:
        tail = " ".join(list(self.stderr)[-3:])
        self.status, self.error = "failed", (why + (f" ({tail})" if tail else ""))[:400]
        log.warning("MCP server %s failed: %s", self.name, self.error)
        events.publish("connections")

    def _drain(self) -> None:
        for raw in self.process.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self.stderr.append(line)

    def _read(self) -> None:
        process = self.process
        for raw in process.stdout:
            try:
                message = json.loads(raw.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue  # a server printing a banner to stdout
            if not isinstance(message, dict):
                continue
            if "id" in message and ("result" in message or "error" in message):
                future = self._waiting.pop(message["id"], None)
                if future is None:
                    continue
                if "error" in message:
                    error = message["error"] or {}
                    future.set_exception(RuntimeError(
                        error.get("message") or "the server returned an error"))
                else:
                    future.set_result(message["result"])
            elif "id" in message and "method" in message:
                # A request from the server (sampling, roots…): none are
                # offered, so each is declined rather than left hanging.
                self._send({"jsonrpc": "2.0", "id": message["id"],
                            "error": {"code": -32601,
                                      "message": "not supported by Naka"}})
        # Whatever was waiting will not be answered now: said at once rather
        # than after a minute's timeout.
        self._abandon("the server exited")
        if self.status == "running" and process is self.process:
            self._fail("the server exited")
            registry.unregister(f"mcp_{slug(self.name)}_")

    def _abandon(self, why: str) -> None:
        waiting, self._waiting = self._waiting, {}
        for future in waiting.values():
            if not future.done():
                future.set_exception(RuntimeError(why))

    # ------------------------------------------------------------ messages

    def _send(self, message: dict) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("the server is not running")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write:
            self.process.stdin.write(data)
            self.process.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method,
                    **({"params": params} if params else {})})

    def request(self, method: str, params: dict | None = None,
                timeout: float = CALL_SECONDS):
        with self._lock:
            self._next += 1
            ident = self._next
        future: Future = Future()
        self._waiting[ident] = future
        self._send({"jsonrpc": "2.0", "id": ident, "method": method,
                    **({"params": params} if params is not None else {})})
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            self._waiting.pop(ident, None)
            raise RuntimeError(f"no answer to {method} within {timeout:.0f}s") \
                from None

    def _list_tools(self) -> list[dict]:
        tools, cursor = [], None
        for _ in range(20):
            page = self.request("tools/list",
                                {"cursor": cursor} if cursor else {})
            tools += page.get("tools", [])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        return tools

    # --------------------------------------------------------------- tools

    def asks(self, tool: dict) -> bool:
        """Whether a call to this tool waits for a yes, tainted or not."""
        policy = self.spec.get("confirm", "writes")
        if policy == "never":
            return False
        if policy == "always":
            return True
        hints = tool.get("annotations") or {}
        return not hints.get("readOnlyHint", False)

    def _register(self) -> None:
        prefix = f"mcp_{slug(self.name)}_"
        registry.unregister(prefix)
        for tool in self.tools:
            name = (prefix + slug(tool.get("name", "")))[:64]
            schema = tool.get("inputSchema") or {}
            remote = tool.get("name", "")
            title = (tool.get("annotations") or {}).get("title") or \
                tool.get("title") or remote
            registry.register(registry.Tool(
                name=name,
                description=f"[{self.name}] {tool.get('description') or title}"[:1000],
                parameters=schema.get("properties") or {},
                handler=self._handler(remote),
                required=list(schema.get("required") or []),
                power=f"mcp.{self.name}",
                confirm=lambda arguments, tainted: tainted,
                label=f"{title}",
                summary=(tool.get("description") or "")[:160],
                untrusted=True,
                dynamic_destructive=self.asks(tool),
            ))

    def _handler(self, remote: str):
        def run(**arguments):
            result = self.request("tools/call",
                                  {"name": remote, "arguments": arguments})
            text = render(result)
            if result.get("isError"):
                raise RuntimeError(text or "the tool reported an error")
            return f"<<untrusted tool output>>\n{text}\n<</untrusted tool output>>"
        return run

    def view(self) -> dict:
        return {"name": self.name, "command": self.spec.get("command"),
                "args": self.spec.get("args", []),
                "env_keys": sorted((self.spec.get("env") or {}).keys()),
                "enabled": not self.spec.get("disabled", False),
                "confirm": self.spec.get("confirm", "writes"),
                "status": self.status, "error": self.error,
                "tools": [{"name": t.get("name"),
                           "description": (t.get("description") or "")[:200],
                           "asks": self.asks(t)} for t in self.tools]}


def render(result: dict) -> str:
    """A tools/call result as text for the model."""
    parts = []
    for item in result.get("content") or []:
        kind = item.get("type")
        if kind == "text":
            parts.append(item.get("text", ""))
        elif kind == "resource":
            resource = item.get("resource") or {}
            parts.append(resource.get("text") or f"(resource {resource.get('uri', '')})")
        elif kind in ("image", "audio"):
            parts.append(f"({kind} returned, not shown)")
        elif kind == "resource_link":
            parts.append(f"(link: {item.get('name') or item.get('uri', '')})")
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    text = "\n".join(p for p in parts if p).strip() or "(no output)"
    if len(text) > RESULT_CHARS:
        text = text[:RESULT_CHARS] + f"\n…(cut; {len(text) - RESULT_CHARS} more characters)"
    return text


# ---------------------------------------------------------------- servers

_servers: dict[str, Server] = {}
_lock = threading.Lock()


def running(name: str) -> bool:
    server = _servers.get(name)
    return server is not None and server.status == "running"


def servers() -> dict[str, Server]:
    return _servers


def start_all() -> None:
    """Start every enabled server, each in its own thread: one slow npx must
    not hold the others up."""
    for name, spec in load().items():
        server = _servers.setdefault(name, Server(name, spec))
        server.spec = spec
        if not spec.get("disabled") and server.status not in ("running", "starting"):
            threading.Thread(target=server.start, daemon=True,
                             name=f"mcp-start-{name}").start()


def restart(name: str) -> Server:
    spec = load().get(name)
    if spec is None:
        raise KeyError(name)
    with _lock:
        server = _servers.get(name)
        if server is not None:
            server.stop()
        server = _servers[name] = Server(name, spec)
    if not spec.get("disabled"):
        threading.Thread(target=server.start, daemon=True,
                         name=f"mcp-start-{name}").start()
    return server


def remove(name: str) -> None:
    with _lock:
        server = _servers.pop(name, None)
    if server is not None:
        server.stop()


def stop_all() -> None:
    for server in list(_servers.values()):
        server.stop()


def wait_idle(seconds: float = 5.0) -> None:
    """For tests: until no server is still starting."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and any(
            s.status == "starting" for s in _servers.values()):
        time.sleep(0.05)
