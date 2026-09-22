# Running Naka

Native Windows. The WSL2 + Docker setup this project started on is preserved
at the `legacy-wsl-docker` tag and is no longer maintained.

## Day to day

Double-click `naka.pyw`, or from the repo:

```powershell
uv sync --group tray
uv run python -m tray
```

That is the whole app: a tray icon, the panel in its own window, and the
push-to-talk key (right Ctrl by default) held for the whole machine, so it
works whatever is in front. It starts the server itself and stops it on Quit.
Closing the window leaves Naka in the tray. "Start with Windows" is in the
tray menu and in Settings.

`scripts\naka.ps1` does the same from a terminal — `start`, `stop`, `status`,
`unload`, `load`, `panel`, `logs`. Windows refuses unsigned scripts until you
allow your own (`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`), or
run it as `powershell -ExecutionPolicy Bypass -File scripts\naka.ps1 status`.

The server alone, for working on it, is still:

```powershell
uv run uvicorn server.main:app --host 127.0.0.1 --port 8000
```

If a server is already answering on the port, the tray uses it rather than
starting its own. `uv run python client/ptt.py` is push-to-talk from a
terminal, with no window.

Always `127.0.0.1`, never `localhost`: on Windows the name resolves to `::1`
first, and against an IPv4-only server every new connection stalls on that
attempt before falling back — a flat ~2.4 s per reply.

## Where things live

| | |
|---|---|
| `%LOCALAPPDATA%\Naka\config` | your settings, voice, persona, facts, tools — what the panel edits |
| `%LOCALAPPDATA%\Naka\logs` | `turns.jsonl`, `audit.jsonl` |
| `%LOCALAPPDATA%\Naka\models` | the GGUF weights |
| `%LOCALAPPDATA%\Naka\runtime\llama` | `llama-server.exe` and its CUDA DLLs |
| `%LOCALAPPDATA%\Naka\cache\hf` | Whisper and Kokoro weights |
| `Documents\Naka Notes` | notes |

`config/` in the repo holds the shipped defaults only. Each file is copied into
the data directory the first time it is missing, and never overwritten after.

## Freeing the GPU for a game

Naka holds about 9.3 GB while resident, and a recent game wants 10-14 GB of
the card's 16 GB. Residency is deliberate — reloading per utterance would cost
seconds every time — so it has to be released explicitly:

```powershell
curl.exe -X POST http://127.0.0.1:8000/ops/unload   # frees ~9.3 GB
curl.exe -X POST http://127.0.0.1:8000/ops/load     # warmed and answering
curl.exe http://127.0.0.1:8000/ops/status
```

`/ops/unload` releases the Python-side models *and* stops `llama-server.exe`,
which is the larger share. `/ops/load` waits for it to actually answer before
reporting ready, rather than merely starting it.

These are operator controls and are deliberately not registered as tools: the
model cannot unload itself mid-sentence, or be talked into it.

## After changing anything

```powershell
uv run python eval/regression.py            # replay the 20 reference phrases
uv run python eval/regression.py --agentic  # same, with tools offered
uv run python eval/test_guardrails.py       # safety checks, no model needed
```

`--baseline` records the current timings; later runs flag anything more than
50% slower. The regression set will not tell you whether the answers are
*good* — that needs ears — but it catches what is easy to miss: something
stopped answering, something got slow, the voice stopped coming out.

## Logs

| file | what |
|---|---|
| `turns.jsonl` | one line per exchange: what was heard, the full prompt, what was said, timings |
| `audit.jsonl` | every tool call with its arguments, including refusals |

## Building Naka.exe

```powershell
uv run --no-sync python packaging\build.py
```

Needs Node (the version in `ui/.nvmrc`) and `uv` on `PATH`. It builds the UI,
freezes the tray in a venv of its own (`build\venv`, the `tray` and `build`
dependency groups only — never torch), and lays out `build\dist\Naka` with the
server's source, the panel, the default config and the manifests beside
`Naka.exe`. It stops if the result is over 150 MB, which only happens when
something heavy got bundled.

On a machine that already has the runtime — this one — there is no need to
download it again to try the installed app:

```powershell
.\.venv\Scripts\python.exe scripts\dev_setup_state.py --venv .venv
```

It links `runtime\venv` in the data folder to the checkout's and writes the
state file setup would have written, so `Naka.exe` starts in the tray. A real
install runs the real steps.

Run as built, `Naka.exe` opens the setup wizard until setup has finished, then
the tray. `NAKA_DATA_DIR` points it at another data folder for testing, and
`NAKA_PYTHON` at an existing venv instead of the one setup installs.

## The installer

`packaging/build.py` compiles it at the end, when Inno Setup 7 is installed
(`build\Naka-setup-<version>.exe`, about 28 MB). It installs per user into
`%LOCALAPPDATA%\Programs\Naka` with no administrator prompt, offers two boxes
— start with Windows, run setup now — and installs the WebView2 runtime only
if the machine has none.

Uninstalling removes the program and asks, once, whether to delete
`%LOCALAPPDATA%\Naka` as well: the models, settings and history, about 15 GB.
The default is No. Answering Yes cannot be undone.

## Licences

`THIRD-PARTY-NOTICES.md` lists everything Naka is built on, and ships beside
`Naka.exe`. Regenerate it after changing dependencies:

```powershell
uv run python packaging\notices.py
```

It reads the packages actually installed, and carries the models' own terms —
Gemma's in particular are worth reading before shipping anything built on it.
