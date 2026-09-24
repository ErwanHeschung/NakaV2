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
import time
from datetime import datetime
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

# Pages that are feeds, logins or video: reading them gives nothing useful.
NO_READ = ("instagram.com", "facebook.com", "tiktok.com", "youtube.com",
           "x.com", "twitter.com", "linkedin.com", "reddit.com", "pinterest.")
# How long a search may spend reading its best result.
READ_BUDGET = 6.0
PASSAGE_CHARS = 900
_STOP = {"the", "and", "for", "what", "when", "who", "whom", "which", "where",
         "how", "does", "did", "was", "were", "are", "is", "this", "that",
         "with", "from", "new", "latest", "last", "next", "current", "come",
         "out", "released", "release", "date", "about", "there", "any", "have",
         "has", "will", "can", "you", "year", "today", "now", "right"}
_DATEISH = re.compile(r"\b(?:19|20)\d\d\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|"
                      r"sep|oct|nov|dec)[a-z]*\b|\b(?:mon|tues|wednes|thurs|fri|"
                      r"satur|sun)day\b", re.IGNORECASE)


# Words a question uses that the sentence answering it rarely does: "who won"
# is answered by "beat", "is it out" by "released".
_ALIKE = {
    "won": {"beat", "defeated", "winner", "champion", "title", "victory"},
    "win": {"beat", "defeated", "winner", "champion", "title", "victory"},
    "winner": {"won", "beat", "defeated", "champion"},
    "price": {"$", "€", "£", "costs", "priced"},
    "cost": {"$", "€", "£", "price", "priced"},
    "episode": {"airs", "aired", "premiere", "premiered", "streaming"},
    "version": {"release", "released", "stable"},
    "score": {"beat", "defeated", "final"},
}


def _today() -> str:
    return datetime.now().strftime("%A %d %B %Y")


def _passages(text: str, query: str) -> str:
    """The sentences of a page that bear on the query, in page order.

    Scored by how many of the query's words they contain, with a point for a
    date: most of what people look up is "when" or "who won", and the
    sentence that answers it nearly always carries a date.
    """
    words = {w for w in re.findall(r"\w+", query.lower())
             if len(w) > 2 and w not in _STOP}
    for word, alike in _ALIKE.items():
        if word in words:
            words |= alike
    now = datetime.now()
    month = now.strftime("%B").lower()
    next_month = datetime(now.year + now.month // 12, now.month % 12 + 1, 1) \
        .strftime("%B").lower()
    scored = []
    for index, sentence in enumerate(re.split(r"(?<=[.!?])\s+|\n+", text)):
        sentence = sentence.strip()
        if not 25 <= len(sentence) <= 400:
            continue
        low = sentence.lower()
        score = sum(1 for w in words if w in low)
        if score and _DATEISH.search(sentence):
            score += 1
            # "Latest" and "next" live near today: a schedule's rows for this
            # month beat its first rows from spring.
            if month in low or next_month in low:
                score += 2
        if score:
            scored.append((score, index, sentence))
    best = sorted(scored, key=lambda s: (-s[0], s[1]))[:8]
    out, used = [], 0
    for _, _, sentence in sorted(best, key=lambda s: s[1]):
        if used + len(sentence) > PASSAGE_CHARS:
            break
        out.append(sentence)
        used += len(sentence)
    return " ".join(out)


def _read_best(results: list[dict], query: str) -> str:
    """Passages from the first result worth reading, within a time budget.

    A 12B model given only snippets answered from them, or from memory,
    and rarely chose to open a page — so a question whose answer sat one
    click away came back as "a few days ago" instead of a date. Reading the
    best result here is what a person does after a search anyway.
    """
    deadline = time.monotonic() + READ_BUDGET
    for index, result in enumerate(results[:3], 1):
        url = result.get("url", "")
        host = urlsplit(url).hostname or ""
        if not url or any(bad in host for bad in NO_READ):
            continue
        if time.monotonic() > deadline:
            break
        try:
            final, page = _download(url, timeout=max(1.0, deadline - time.monotonic()))
        except (ValueError, httpx.HTTPError) as e:
            log.info("could not read %s: %s", url, e)
            continue
        found = _passages(_readable(page, final), query)
        if found:
            return f"\n\nFrom result {index} ({host}): {found}"
    return ""


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


# Tried in order until one answers. Named engines rather than ddgs's "auto",
# which tries several in turn and took 19s against DuckDuckGo's 1.3s for the
# same query. More than two, because these are scraped rather than APIs: in a
# bench of a few hundred searches roughly one in eight came back empty from
# DuckDuckGo, and sometimes from Bing too, for queries that work a minute later.
BACKENDS = ("duckduckgo", "bing", "brave", "yahoo", "mojeek")
SEARCH_BUDGET = 12.0


def _duckduckgo(query: str, count: int) -> list[dict]:
    """The free engines, each until one returns something."""
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    deadline = time.monotonic() + SEARCH_BUDGET
    # DuckDuckGo twice: an empty answer from it is usually a brief refusal,
    # and it is the fastest and best of these when it answers.
    for backend in ("duckduckgo", *BACKENDS):
        if time.monotonic() > deadline:
            break
        try:
            found = DDGS(timeout=int(TIMEOUT)).text(
                query, max_results=count, backend=backend)
        except DDGSException as e:
            log.info("%s search failed (%s)", backend, e)
            continue
        # Ads, which the scraped engines pass through as results: a Bing ad
        # for "Claude 4.6" came first and was taken as the newest model.
        found = [r for r in found or [] if not _is_ad(r.get("href", ""))]
        if found:
            return [{"title": r.get("title", ""), "url": r.get("href", ""),
                     "snippet": r.get("body", "")} for r in found]
    return []


def _is_ad(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return (("bing.com" in host and parts.path.startswith("/aclick"))
            or ("duckduckgo.com" in host and parts.path.startswith("/y.js"))
            or host.startswith("ad.") or "doubleclick" in host
            or "googleadservices" in host)


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
    # Today's date beside the results, not only in the system prompt: "the
    # latest", "next" and "is it out yet" all need the comparison, and the
    # model made it far more often with the date in front of it.
    return f"Today is {_today()}.\n" + untrusted(text + _read_best(results, query))


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


def _download(url: str, timeout: float = TIMEOUT) -> tuple[str, str]:
    """The page's final URL and its text, following redirects by hand.

    By hand so that every hop is checked: a public page that redirects to
    127.0.0.1 is exactly the way around a check made only on the first URL.
    """
    with httpx.Client(timeout=timeout, follow_redirects=False,
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
    return f"Today is {_today()}.\n" + untrusted(f"{final}\n\n{text}")
