# Running Naka

## Day to day

```bash
./scripts/start-naka.sh      # container + server, idempotent
tail -f logs/naka.log
```

Then run the client on the **Windows** side, not in WSL:

```
pip install -r client/requirements.txt
python client/ptt.py
```

Hold right-ctrl to talk.

## Freeing the GPU for a game

Naka holds about 9.3 GB while resident, and a recent game wants 10-14 GB of
the card's 16 GB. Residency is deliberate — reloading per utterance would cost
seconds every time — so it has to be released explicitly:

```bash
curl -X POST localhost:8000/ops/unload   # ~1.8s, frees ~9.3 GB
curl -X POST localhost:8000/ops/load     # ~25s, warmed and answering
curl localhost:8000/ops/status
```

`/ops/unload` releases the Python-side models *and* stops the llama.cpp
container, which is the larger share. `/ops/load` waits for the container to
actually answer before reporting ready, rather than merely starting it.

These are operator controls and are deliberately not registered as tools: the
model cannot unload itself mid-sentence, or be talked into it.

## Starting at logon

Without this it quietly falls out of use — anything needing two terminal
commands before you can speak to it does not get spoken to.

In Windows Task Scheduler, create a task that runs at logon:

- **Program:** `wsl.exe`
- **Arguments:** `-d Ubuntu -- /home/erwan/projects/NakaV2/scripts/start-naka.sh`
- Tick **Run whether user is logged on or not** off; it needs the desktop session.

Or from an elevated PowerShell:

```powershell
$action  = New-ScheduledTaskAction -Execute 'wsl.exe' `
  -Argument '-d Ubuntu -- /home/erwan/projects/NakaV2/scripts/start-naka.sh'
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName 'Naka' -Action $action -Trigger $trigger
```

## After changing anything

```bash
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
| `logs/naka.log` | human-readable; watch it work |
| `logs/turns.jsonl` | one line per exchange: what was heard, the full prompt, what was said, timings |
| `logs/audit.jsonl` | every tool call with its arguments, including refusals |
