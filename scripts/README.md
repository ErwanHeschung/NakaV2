# Running Naka

Native Windows. The WSL2 + Docker setup this project started on is preserved
at the `legacy-wsl-docker` tag and is no longer maintained.

## Day to day

From the repo, in PowerShell:

```powershell
uv run uvicorn server.main:app --host 127.0.0.1 --port 8000
```

The server starts `llama-server.exe` itself the first time something needs the
language model, and stops it again when idle. The panel is at
<http://127.0.0.1:8000/ui/>. Hold the push-to-talk key (right Ctrl by default)
with the panel focused, or run `uv run python client/ptt.py` to talk from
anywhere.

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
