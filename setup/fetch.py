"""Download one file, resumably, and prove it is the right one.

Everything setup fetches goes through here — uv, llama.cpp, the language model.
The model alone is 6.5 to 9 GB, so the rules are the ones a slow or flaky line
needs:

- Bytes land in NAME.part and the file only takes its real name once its
  sha256 matches. A truncated download can therefore never be mistaken for a
  finished one, which is the failure that would otherwise surface much later
  as "she never answers".
- An interrupted download resumes with an HTTP Range request from where the
  .part left off, instead of starting 7 GB over.
- The hash is computed while the bytes arrive, so an 8 GB file is never read
  twice.
- For a language model, the first four bytes must be "GGUF". An HTML error
  page, a Git LFS pointer or a blob/main link instead of resolve/main is then
  caught within the first second, not after the whole download.
"""

import hashlib
import struct
import time
from pathlib import Path

import httpx

CHUNK = 1 << 20
# Progress is reported at most this often. The panel's event hub drops events
# for a client that falls more than 32 behind, so an unthrottled byte counter
# would freeze the very bar it is meant to move.
PROGRESS_EVERY = 0.25


class FetchError(Exception):
    """The wrong thing, or a refusal: retrying will not help."""


class _Transient(Exception):
    """A server having a bad moment. Worth retrying, and keeping the .part for."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def check_gguf_header(head: bytes) -> None:
    if len(head) < 8:
        return
    if head[:4] != b"GGUF":
        preview = head[:40].decode("utf-8", errors="replace").strip()
        raise FetchError(
            "this is not a GGUF model file"
            + (f" — it starts with {preview!r}" if preview else "")
            + ". If it came from Hugging Face, the link should contain "
              "/resolve/, not /blob/.")
    version = struct.unpack("<I", head[4:8])[0]
    if version not in (2, 3):
        raise FetchError(f"unsupported GGUF version {version}")


def download(url: str, dest: Path, *, sha256: str | None = None,
             size: int | None = None, gguf: bool = False,
             on_progress=None, attempts: int = 4) -> str:
    """Fetch `url` to `dest`. Returns the file's sha256.

    Skips the download if `dest` already exists with the expected hash. With
    no expected hash — a link someone pasted — the hash is computed and
    returned so it can be recorded.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if sha256 is None or sha256_of(dest) == sha256:
            return sha256_of(dest) if sha256 is None else sha256
        dest.unlink()  # present but wrong: fetch it again

    part = dest.with_name(dest.name + ".part")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            digest = _fetch(url, part, size, gguf, on_progress)
            break
        except FetchError:
            # Not a network hiccup but the wrong file entirely — an HTML page,
            # an LFS pointer. Resuming it would only append to the wrong bytes.
            part.unlink(missing_ok=True)
            raise
        except (httpx.HTTPError, OSError, _Transient) as e:
            # The .part stays, so the next attempt resumes rather than restarts.
            last_error = e
            time.sleep(min(2 ** attempt * 2, 30))
    else:
        raise FetchError(f"could not download {url}: {last_error}")

    if sha256 is not None and digest != sha256:
        part.unlink(missing_ok=True)
        raise FetchError(f"{dest.name} did not match its checksum "
                         f"(got {digest[:12]}…, expected {sha256[:12]}…)")
    part.replace(dest)
    return digest


def _fetch(url, part: Path, size, gguf, on_progress) -> str:
    digest = hashlib.sha256()
    have = part.stat().st_size if part.exists() else 0
    if have:
        # Resuming: the bytes already on disk are part of the hash.
        with part.open("rb") as f:
            while block := f.read(CHUNK):
                digest.update(block)
        if gguf:
            with part.open("rb") as f:
                check_gguf_header(f.read(8))

    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True,
                      timeout=httpx.Timeout(30.0, read=120.0)) as response:
        if response.status_code == 416 and have:
            # The .part is already complete; the server has nothing more.
            return digest.hexdigest()
        if have and response.status_code == 200:
            # The server ignored the Range header: start over.
            have = 0
            digest = hashlib.sha256()
            part.unlink(missing_ok=True)
        elif response.status_code >= 500 or response.status_code == 429:
            raise _Transient(f"{url} answered HTTP {response.status_code}")
        elif response.status_code not in (200, 206):
            raise FetchError(f"{url} answered HTTP {response.status_code}")

        total = size
        if total is None:
            length = response.headers.get("Content-Length")
            total = have + int(length) if length else None

        done = have
        head = b""
        last = 0.0
        with part.open("ab") as f:
            for block in response.iter_bytes(CHUNK):
                if gguf and done < 8:
                    head += block[:8 - len(head)]
                    if len(head) >= 8:
                        check_gguf_header(head)
                f.write(block)
                digest.update(block)
                done += len(block)
                now = time.monotonic()
                if on_progress and now - last >= PROGRESS_EVERY:
                    last = now
                    on_progress(done, total)
        if on_progress:
            on_progress(done, total)
    return digest.hexdigest()
