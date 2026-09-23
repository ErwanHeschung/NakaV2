# Architecture

How Naka is put together, for anyone working on it. What she does for the person using her is in the [README](README.md).

## Overview

Naka is three processes on one Windows machine, all bound to `127.0.0.1`:

```
 Naka.exe (tray)                       python server (FastAPI, :8000)          llama-server.exe (:8080)
 ┌──────────────────────┐   HTTP      ┌─────────────────────────────┐  HTTP   ┌──────────────────────┐
 │ push to talk hook    │ ──────────▶ │ /converse                   │ ──────▶ │ Gemma 4 12B, CUDA    │
 │ mic in, speaker out  │ ◀────────── │  STT → agent loop → TTS     │ ◀────── │ OpenAI compatible    │
 │ tray icon, menu      │  PCM stream │ panel API, SSE events       │ stream  │ chat completions     │
 │ panel window         │             │ Whisper + Kokoro on the GPU │         └──────────────────────┘
 └──────────────────────┘             └─────────────────────────────┘
```

| Process | Code | Role |
|:-|:-|:-|
| Tray | `tray/` | The one thing a person starts. Holds the global talk key, records and plays audio, shows the panel in a pywebview window, supervises the server. Frozen into `Naka.exe` with PyInstaller. |
| Server | `server/` | Speech to text, the agent loop, text to speech, memory, tools, and the HTTP API the tray and panel use. Runs under the runtime venv's Python, which holds torch. |
| Language model | `server/llamacpp.py`, `server/ops.py` | A prebuilt llama.cpp release, started by the server as a child process when needed and stopped when idle. |

Each parent puts its children in a Windows job object with kill on close (`server/winjob.py`). However the tray ends, cleanly or not, the server and llama-server end with it, and nothing is left holding the GPU.

## A spoken turn

1. **Capture.** The tray's keyboard hook sees the talk key go down and calls `/ops/wake`, so any model reload overlaps with the person speaking. Audio is recorded while the key is held (`client/audio.py`).
2. **Speech to text.** `/converse` receives a WAV. faster-whisper `large-v3-turbo` transcribes it, with the language forced rather than detected (`server/stt.py`).
3. **Prompt.** `server/memory.py` assembles persona, facts, summary and recent turns, stable parts first so llama.cpp can reuse its cached prefix. The clock and wake notes go just before the user's message for the same reason.
4. **Language model.** The reply streams from llama-server and is cut into sentences as they complete (`server/llm.py`). With tools on, the first delta already says whether the model is speaking or calling a tool, so offering tools costs no waiting.
5. **Speech.** Each sentence is synthesised by Kokoro as soon as it exists (`server/tts.py`), then passed through the voice chain (`server/dsp.py`: pitch and formant shift, EQ, chorus, compression, limiter) built on PyAV filters.
6. **Playback.** Audio streams back as raw PCM for the tray and panel, or Ogg Opus for the terminal client, so the first sentence plays while the rest is still being written.
7. **Afterwards.** The turn is logged (`server/turnlog.py`), and memory is reconciled in the background: facts added or revised, the summary rolled forward.

A single `asyncio.Lock` serialises GPU work in the server. One inference at a time is faster on one card than several fighting over it.

## Agent loop and tools

`server/agent.py` runs the loop: the model may call tools, see their results, and call more until it answers. `server/tools/registry.py` is the gate every call goes through.

| Guardrail | Where |
|:-|:-|
| A tool must be registered in code **and** enabled in `tools.yaml` | `registry.get` |
| Arguments outside a tool's schema, or missing ones, are refused | `registry.call` |
| Step ceiling and wall clock timeout per request (5 and 30 s, or 12 and 180 s while a power is on) | `agent.limits` |
| Spoken confirmation before anything destructive; the loop suspends and resumes after a yes | `agent._loop`, `agent.resolve_pending` |
| Kill switch: stops the loop, and ends a running command with its process tree | `POST /agent/stop` |
| Every call audited with its arguments and outcome, including refusals and declines | `logs/audit.jsonl` |

**Powers.** Two groups of tools are also gated by a switch in `settings.toml`, read on every call so the panel can flip them live.

- `server/tools/web.py`: `web_search` (DuckDuckGo with Bing behind it, or Brave with an API key) and `fetch_page` (httpx plus trafilatura for the main text). Any address resolving to loopback, private, link local or reserved ranges is refused, and checked again at every redirect. Results are wrapped as untrusted content.
- `server/tools/shell.py`: `run_command` runs PowerShell as the user. Whether it asks first is decided per command by PowerShell's own parser: read only means every command anywhere in the input is on an allowlist of commands that only look, with no redirection, method call, assignment or call operator. Anything else, and anything that fails to parse, needs a yes. Once web content has entered a turn, every command in that turn needs one. Each command runs in its own job object with a memory cap and a timeout.

While a chain runs, results older than two steps are shortened so they do not push the persona out of the context window.

## Memory

Flat files, no vector store: one person and one conversation do not need retrieval.

| Piece | Stored in | Lifetime |
|:-|:-|:-|
| Persona | `config/persona.md` | Edited by the person |
| Facts | `config/facts.json` | Capped list, maintained after each turn by a reconciler that adds, revises or drops facts |
| Summary | server memory | Older turns folded into a rolling summary; starts empty when the server restarts |
| Recent turns | server memory | The last few exchanges, replayed with their tool calls one block per step |

## Panel

The panel is TypeScript compiled with `tsc` into `ui/public/js`, with no bundler and no framework. It is served by the server at `/ui` and shown by the tray in a WebView2 window.

It reads state over plain HTTP endpoints (`server/panel.py`) and listens on `/events`, a Server Sent Events stream that announces a topic whenever something changes (`server/events.py`). Events carry no state: the panel re-reads what it shows. Timers ring down the same stream.

## GPU residency

Naka holds about 9.5 GB of VRAM: roughly 7.7 GB for llama-server and 1.7 GB for Whisper and Kokoro. Loading costs about 8 seconds, so the models stay resident while in use, and `server/ops.py` releases them after a configurable idle period or on request from the tray. The talk key starts the reload, so most of it is absorbed while the person is still speaking.

Operational controls (`/ops/*`) are deliberately not tools: the model cannot unload itself or stop its own server.

## Files on disk

`server/paths.py` is the only module that knows where things live.

| Root | Default | Holds |
|:-|:-|:-|
| App | `%LOCALAPPDATA%\Programs\Naka` | Code, built panel, shipped default config. Replaced on upgrade, never written at runtime. |
| Data | `%LOCALAPPDATA%\Naka` | The person's config, logs, models, runtime venv, caches. Survives upgrades. |

Config files are seeded from the shipped defaults once and never overwritten. Settings and `tools.yaml` are read as the shipped file with the person's laid over it, so a key or tool added in a later version appears without rewriting theirs. At runtime Hugging Face is forced offline, so a missing model fails loudly instead of downloading mid conversation.

## Setup and packaging

- **Installer.** `packaging/build.py` builds the panel, freezes the tray into `Naka.exe`, lays out the server source beside it and packs an Inno Setup installer (`packaging/naka.iss`) of about 28 MB. No runtime or model is inside it.
- **First run.** `setup/` is a wizard in its own window (`ui/public/setup.html`). It checks the GPU and driver, then fetches uv, a managed Python, the runtime venv from `uv.lock`, llama.cpp, the speech models and the chosen language model. Every artifact is pinned in `packaging/manifest/*.json` by version and SHA-256. Progress is saved per step, so an interrupted setup resumes.
- **Notices.** `packaging/notices.py` regenerates `THIRD-PARTY-NOTICES.md` from the packages actually installed, plus the pieces without Python metadata.

## Repository layout

```
server/        FastAPI app: STT, LLM client, agent, tools, memory, voice, panel API
  tools/       registry and allowlist, builtin tools, web, PowerShell
tray/          Naka.exe: tray icon, push to talk, panel window, server supervisor
client/        audio helpers shared with the tray, and a terminal push to talk client
setup/         first run wizard and its steps
ui/            panel and setup pages (TypeScript, CSS, vendored Lucide icons)
config/        shipped defaults: settings, voice, persona, tools, facts
packaging/     build script, installer script, pinned download manifests
eval/          benchmarks, results, regression phrases, guardrail tests
scripts/       naka.ps1 and notes on running from a checkout
```

## Testing

| Command | Checks |
|:-|:-|
| `uv run python eval\test_guardrails.py` | The agent's guardrails with scripted model replies, and real PowerShell for classification, timeouts and the kill switch |
| `uv run python eval\regression.py` | Reference phrases replayed through the running server: nothing stopped answering, got slow or lost its voice |
| `uv run python eval\e2e_bench.py` | Time to first sound |
| `npm run check` in `ui/` | Types, lint and formatting of the panel |

Benchmarks and the decisions they led to (Gemma over Qwen3, Kokoro over Chatterbox, reasoning tokens off) are written up in [eval/RESULTS.md](eval/RESULTS.md).
