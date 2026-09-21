"""Fetch Whisper's and Kokoro's small files, and record their revision.

Both libraries download their weights from Hugging Face the first time they
are used, unless told not to — which is how 1.9 GB used to arrive, unpinned
and unchecked, the first time the server started. The server now runs with
HF_HUB_OFFLINE set, so these have to be in place before it ever starts.

Runs inside the runtime venv, because huggingface_hub lives there:

    <venv>\\Scripts\\python.exe -m setup.prefetch --manifest runtime.json --hf-home DIR

Deliberately imports nothing from server/: server.paths forces offline mode on
import, which is the one thing this must not have. Prints one JSON object per
line so setup can follow along.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def emit(**event) -> None:
    print(json.dumps(event), flush=True)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--hf-home", required=True)
    args = parser.parse_args()

    # Before huggingface_hub is imported: it reads these once, at import.
    os.environ["HF_HOME"] = args.hf_home
    os.environ.pop("HF_HUB_OFFLINE", None)
    # Windows without Developer Mode cannot symlink; the cache copies instead
    # and would otherwise say so once per file.
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    # Plain HTTPS rather than Hugging Face's Xet transfer. Xet reassembles a
    # file out of sight before writing it, so nothing shows on disk until the
    # end — a progress bar stuck at zero for 1.6 GB — and on a modest line it
    # throttled itself to one connection and crawled. Plain HTTPS writes as it
    # goes, and resumes from the partial file if interrupted.
    os.environ["HF_HUB_DISABLE_XET"] = "1"

    from huggingface_hub import snapshot_download

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    for entry in manifest["speech"]:
        repo, revision = entry["repo"], entry["revision"]
        emit(event="start", repo=repo)
        try:
            # The large files are already in place, fetched by setup's own
            # resumable downloader. Only the rest comes from here.
            small = [p for p in entry["allow"] if p not in entry.get("verify", {})]
            folder = Path(snapshot_download(repo, revision=revision,
                                            allow_patterns=small))
        except Exception as e:
            emit(event="error", repo=repo, reason=f"{type(e).__name__}: {e}")
            return 1

        for name, expect in entry.get("verify", {}).items():
            path = folder / name
            if not path.exists() or path.stat().st_size != expect["bytes"] \
                    or sha256_of(path) != expect["sha256"]:
                emit(event="error", repo=repo,
                     reason=f"{name} did not match its checksum")
                return 1

        # Pinning to a commit does not record it as "main". But at runtime
        # faster-whisper and Kokoro ask for main, and offline, an unresolvable
        # main is a missing model even with every file present. So say it.
        refs = folder.parent.parent / "refs"
        refs.mkdir(parents=True, exist_ok=True)
        (refs / "main").write_text(revision, encoding="utf-8")

        emit(event="done", repo=repo, path=str(folder))
    return 0


if __name__ == "__main__":
    sys.exit(main())
