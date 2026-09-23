"""Looking things up: a search, and reading what it finds.

The models never leave the machine; these only fetch text for them. Two
things are guarded against. A page is written by a stranger, so whatever comes
back is wrapped as untrusted and the agent treats the rest of the turn
accordingly. And the fetcher is a way for the model to make requests from
this machine, so it refuses anything that resolves inside it or onto the local
network — llama-server and Naka's own API both listen on loopback.
"""

import html
import ipaddress
import logging
import re
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from .. import settings
from .registry import tool

log = logging.getLogger("naka.web")

SEARCH_CHARS = 1500
PAGE_CHARS = 2500
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT = 10.0
# Honest rather than a browser's, and with a URL in it. Measured: Wikipedia
# refuses both a Firefox string with no browser behind it and a bare
# "Naka/0.1", and serves this — its policy asks bots for a way to reach whoever
# runs them. The sites that refuse this refused the fake one too.
USER_AGENT = ("Naka/0.1 (personal voice assistant; "
              "+https://github.com/ErwanHeschung/NakaV2)")

_TAG = re.compile(r"<[^>]+>")


def untrusted(text: str) -> str:
    """Mark text as content, not instructions. The agent's prompt says so."""
    return f"<<untrusted web content>>\n{text}\n<</untrusted web content>>"


def _plain(text: str) -> str:
    return " ".join(html.unescape(_TAG.sub("", text or "")).split())


def _brave(query: str, count: int, key: str) -> list[dict]:
    response = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": count},
        headers={"Accept": "application/json", "X-Subscription-Token": key},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return [{"title": r.get("title", ""), "url": r.get("url", ""),
             "snippet": r.get("description", "")}
            for r in response.json().get("web", {}).get("results", [])]


def _duckduckgo(query: str, count: int) -> list[dict]:
    """DuckDuckGo, with Bing behind it.

    Named engines rather than ddgs's "auto", which tries several in turn and
    took 19s against DuckDuckGo's 1.3s for the same query — the difference
    between an answer and a long silence.
    """
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    for backend in ("duckduckgo", "bing"):
        try:
            found = DDGS(timeout=int(TIMEOUT)).text(
                query, max_results=count, backend=backend)
        except DDGSException as e:
            log.info("%s search failed (%s)", backend, e)
            continue
        if found:
            return [{"title": r.get("title", ""), "url": r.get("href", ""),
                     "snippet": r.get("body", "")} for r in found]
    return []


@tool(
    description=(
        "Search the web. Use it for anything recent, anything you are not "
        "sure of, and anything the user asks you to look up — do not answer "
        "those from memory. Returns titles, links and snippets; read a page "
        "with fetch_page when the snippets are not enough."
    ),
    parameters={
        "query": {"type": "string", "description": "What to search for."},
        "max_results": {"type": "integer",
                        "description": "How many results, default 5."},
    },
    required=["query"],
    power="web",
    label="Search the web",
    summary="DuckDuckGo, or Brave if you give it a key.",
)
def web_search(query: str, max_results: int = 5):
    count = max(1, min(int(max_results), 10))
    key = (settings.POWERS.get("brave_api_key") or "").strip()
    results = _brave(query, count, key) if key else _duckduckgo(query, count)
    if not results:
        return f"No results for {query!r}."
    lines = [f"{i}. {_plain(r['title'])} — {r['url']}\n   {_plain(r['snippet'])}"
             for i, r in enumerate(results, 1)]
    text = "\n".join(lines)
    if len(text) > SEARCH_CHARS:
        text = text[:SEARCH_CHARS] + "… (truncated)"
    return untrusted(text)


def _check_host(url: str) -> None:
    """Refuse a URL that is not http(s) or that lands on this machine or LAN.

    Every address the name resolves to is checked, not just the first: a name
    with one public and one private record would otherwise get through on the
    public one and be connected on the private one.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("only http and https pages can be fetched")
    if not parts.hostname:
        raise ValueError("that is not a full web address")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443)
    except socket.gaierror as e:
        raise ValueError(f"cannot find {parts.hostname}") from e
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast
                or address.is_unspecified):
            raise ValueError(
                f"{parts.hostname} is on this machine or the local network, "
                "which is off limits")


def _download(url: str) -> tuple[str, str]:
    """The page's final URL and its text, following redirects by hand.

    By hand so that every hop is checked: a public page that redirects to
    127.0.0.1 is exactly the way around a check made only on the first URL.
    """
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False,
                      headers={"User-Agent": USER_AGENT,
                               "Accept-Language": "en,fr;q=0.8"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _check_host(url)
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                kind = response.headers.get("content-type", "")
                if not kind.startswith(("text/", "application/xhtml",
                                        "application/json")):
                    raise ValueError(f"that is not a web page ({kind or 'unknown type'})")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        break
                encoding = response.encoding or "utf-8"
                return url, bytes(body[:MAX_BYTES]).decode(encoding, "replace")
    raise ValueError("too many redirects")


def _readable(page: str, url: str) -> str:
    import trafilatura
    text = trafilatura.extract(page, url=url, include_comments=False,
                               include_tables=True, favor_precision=True)
    return text or _plain(page)


def _focused(text: str, focus: str) -> str:
    """Only the paragraphs that mention the focus words, if any do."""
    words = [w for w in re.findall(r"\w+", focus.lower()) if len(w) > 2]
    if not words:
        return text
    kept = [p for p in text.split("\n")
            if any(w in p.lower() for w in words)]
    return "\n".join(kept) if kept else text


@tool(
    description=(
        "Read a web page and get its main text. Give `focus` to keep only the "
        "parts about that, which leaves room for more pages."
    ),
    parameters={
        "url": {"type": "string", "description": "The page's full address."},
        "focus": {"type": "string",
                  "description": "Optional: what you are looking for on it."},
    },
    required=["url"],
    power="web",
    label="Read a page",
    summary="Fetches a page and keeps its main text. Never anything on this PC or your network.",
)
def fetch_page(url: str, focus: str = ""):
    final, page = _download(url.strip())
    text = _readable(page, final)
    if focus:
        text = _focused(text, focus)
    total = len(text)
    if total > PAGE_CHARS:
        text = text[:PAGE_CHARS] + f"… (truncated, {total} characters in all)"
    return untrusted(f"{final}\n\n{text}")
