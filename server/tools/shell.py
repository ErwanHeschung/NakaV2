"""A PowerShell prompt, run as the person, behind their word.

What runs without asking is decided by PowerShell's own parser, not by
matching text: a command is read-only only if every command anywhere in it —
piped, nested in $( ), inside a script block — is on a short list of commands
that only look, and nothing in it redirects to a file, calls a .NET method or
assigns anything. Everything else, including whatever fails to parse, waits for
a spoken yes. A regex over the command line would be fooled by the first alias
or subexpression; the parser is the thing that will actually run it.

Each command runs in a job object of its own, capped in memory and killed —
with every process it started — when it runs too long or the kill switch
trips.
"""

import base64
import functools
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .. import settings, winjob
from .registry import tool

log = logging.getLogger("naka.shell")

OUTPUT_CHARS = 2000
MEMORY_MB = 1024

# Lower-cased, aliases included. Only commands that report on something and
# change nothing. ForEach-Object and Where-Object run script blocks, but the
# parser walks into those, so whatever they would run is checked too.
READ_ONLY = {
    "get-childitem", "gci", "ls", "dir", "get-content", "gc", "cat", "type",
    "select-string", "sls", "get-item", "gi", "get-itemproperty", "gp",
    "test-path", "resolve-path", "rvpa", "split-path", "join-path",
    "measure-object", "measure", "sort-object", "sort", "select-object",
    "select", "where-object", "where", "?", "foreach-object", "foreach", "%",
    "group-object", "group", "compare-object", "get-unique", "get-member", "gm",
    "format-table", "ft", "format-list", "fl", "format-wide", "fw",
    "out-string", "out-host", "write-output", "echo", "write-host",
    "convertto-json", "convertfrom-json", "convertto-csv", "convertfrom-csv",
    "get-process", "ps", "gps", "get-service", "gsv", "get-date",
    "get-location", "pwd", "gl", "get-command", "gcm", "get-help",
    "get-psdrive", "gdr", "get-volume", "get-disk", "get-computerinfo",
    "get-netipaddress", "get-netadapter", "get-filehash", "get-acl",
    "get-culture", "get-timezone", "get-hotfix", "get-ciminstance",
    "get-wmiobject", "gwmi", "get-host", "get-variable", "gv", "get-uptime",
    "whoami", "whoami.exe", "hostname", "hostname.exe", "ipconfig",
    "ipconfig.exe", "systeminfo", "systeminfo.exe", "tasklist",
    "tasklist.exe", "where.exe", "tree", "tree.com", "findstr", "findstr.exe",
    "nvidia-smi", "nvidia-smi.exe",
}

# git is read-only only for these, and only with arguments that keep it so.
GIT_READ = {"status", "log", "diff", "show", "branch", "remote", "rev-parse",
            "ls-files", "blame", "describe", "shortlog", "tag"}
# branch, remote and tag change things when given a name or a flag like -D,
# so for them only listing forms count.
GIT_LISTING_ARGS = {"-a", "-r", "-v", "-vv", "--all", "--list", "-l",
                    "--show-current", "--remotes", "--verbose"}

# Walks the command with PowerShell's parser and reports what is in it. The
# source arrives in an environment variable so no quoting can reach the parser
# differently from how it would reach the shell.
_INSPECT = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $env:NAKA_INSPECT, [ref]$tokens, [ref]$errors)
function Count($type) {
    @($ast.FindAll({ param($n) $n -is $type }, $true)).Count
}
$commands = @($ast.FindAll({ param($n)
    $n -is [System.Management.Automation.Language.CommandAst] }, $true) |
    ForEach-Object {
        @{ name = $_.GetCommandName();
           operator = [string]$_.InvocationOperator;
           args = @($_.CommandElements | Select-Object -Skip 1 |
                    ForEach-Object { $_.Extent.Text }) }
    })
[Console]::OutputEncoding = [Text.Encoding]::UTF8
@{ errors = $errors.Count;
   commands = $commands;
   redirections = Count ([System.Management.Automation.Language.FileRedirectionAst]);
   methods = Count ([System.Management.Automation.Language.InvokeMemberExpressionAst]);
   assignments = Count ([System.Management.Automation.Language.AssignmentStatementAst])
} | ConvertTo-Json -Depth 5 -Compress
"""

# The command runs as a script block built from an environment variable, for
# the same reason. Errors are merged into the output and everything is
# rendered as text, so what the model sees is what a person at the prompt
# would have seen.
_RUN = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "$ProgressPreference = 'SilentlyContinue'; "
    "& ([scriptblock]::Create($env:NAKA_COMMAND)) 2>&1 | Out-String -Width 160; "
    "if ($LASTEXITCODE) { exit $LASTEXITCODE }"
)

_CREATE_NO_WINDOW = 0x08000000

_active = None
_active_lock = threading.Lock()


def _powershell() -> str:
    return (shutil.which("pwsh") or shutil.which("powershell")
            or "powershell.exe")


def _encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _powershell_args(script: str) -> list[str]:
    return [_powershell(), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-EncodedCommand", _encoded(script)]


def workspace() -> Path:
    raw = settings.POWERS.get("workspace") or "~/Documents/Naka Workspace"
    return Path(os.path.expandvars(raw)).expanduser()


@functools.lru_cache(maxsize=256)
def inspect(command: str) -> dict | None:
    """What the parser finds in a command, or None if it could not say."""
    env = dict(os.environ, NAKA_INSPECT=command)
    try:
        done = subprocess.run(
            _powershell_args(_INSPECT), capture_output=True, env=env,
            timeout=15, creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        report = json.loads(done.stdout.decode("utf-8", "replace").strip())
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        log.warning("could not inspect %r: %s", command, e)
        return None
    # ConvertTo-Json flattens a one-element array into the element itself.
    commands = report.get("commands") or []
    report["commands"] = [commands] if isinstance(commands, dict) else commands
    return report


def _git_reads(args: list[str]) -> bool:
    words = [a.strip("'\"") for a in args]
    if not words or words[0] not in GIT_READ:
        return False
    rest = words[1:]
    if any(w.startswith("--output") or w == "-o" for w in rest):
        return False
    if words[0] in ("branch", "remote", "tag"):
        return all(w in GIT_LISTING_ARGS for w in rest)
    return True


def classify(command: str) -> str:
    """"read" if the command only looks, "write" for anything else."""
    report = inspect(command.strip())
    if report is None or report.get("errors"):
        return "write"
    if report.get("redirections") or report.get("methods") \
            or report.get("assignments"):
        return "write"
    if not report["commands"]:
        # Nothing but expressions — `1 + 1`, a string. Harmless.
        return "read"
    for entry in report["commands"]:
        name = (entry.get("name") or "").lower()
        if entry.get("operator") not in ("", "Unknown"):
            return "write"
        if name in ("git", "git.exe"):
            if not _git_reads(entry.get("args") or []):
                return "write"
        elif name not in READ_ONLY:
            return "write"
    return "read"


def _needs_confirmation(arguments: dict, tainted: bool) -> bool:
    # Once a web page has been read this turn, even a listing waits: the page
    # may be what asked for it, and what it lists goes back to the model.
    return tainted or classify(arguments.get("command", "")) != "read"


def _clip(text: str) -> str:
    if len(text) <= OUTPUT_CHARS:
        return text
    head, tail = OUTPUT_CHARS * 3 // 5, OUTPUT_CHARS * 2 // 5
    return (f"{text[:head]}\n… ({len(text) - head - tail} characters elided) …\n"
            f"{text[-tail:]}")


def abort() -> bool:
    """End the command that is running, and everything it started."""
    global _active
    with _active_lock:
        job, _active = _active, None
    if job is None:
        return False
    winjob.close_job(job)
    log.warning("running command killed")
    return True


@tool(
    description=(
        "Run a PowerShell command on the user's Windows PC, as the user, and "
        "get its output. Commands that only read (Get-ChildItem, Get-Content, "
        "Select-String, git status...) run at once; anything that changes "
        "something is read out to the user and waits for their yes. Work in "
        "small steps and check each result before the next. The output is "
        "for you: summarise it, never read it out."
    ),
    parameters={
        "command": {"type": "string",
                    "description": "The PowerShell command line."},
        "cwd": {"type": "string",
                "description": "Folder to run in. Defaults to the workspace."},
    },
    required=["command"],
    power="shell",
    confirm=_needs_confirmation,
    label="Run PowerShell",
    summary="Commands on this PC, as you. Looking runs at once; changing anything waits for your yes.",
)
def run_command(command: str, cwd: str = ""):
    global _active
    command = command.strip()
    if not command:
        raise ValueError("there is no command to run")

    folder = Path(os.path.expandvars(cwd)).expanduser() if cwd else workspace()
    if not cwd:
        folder.mkdir(parents=True, exist_ok=True)
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")

    timeout = int(settings.POWERS.get("command_timeout", 20))
    env = dict(os.environ, NAKA_COMMAND=command)
    process = subprocess.Popen(
        _powershell_args(_RUN), cwd=folder, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        job = winjob.command_job(process.pid, MEMORY_MB)
    except OSError as e:
        # Uncontained, a runaway could outlive the timeout and the kill
        # switch both, so it does not get to run at all.
        process.kill()
        process.wait()
        raise RuntimeError(f"could not contain the command: {e}") from e

    with _active_lock:
        _active = job
    timed_out = False
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        # Taken under the lock so the kill switch cannot close it a second
        # time: a closed handle's value can be reused by the next one opened.
        with _active_lock:
            ours = _active is job
            if ours:
                _active = None
        if ours:
            winjob.close_job(job)
        output, _ = process.communicate()
    finally:
        with _active_lock:
            still_ours = _active is job
            _active = None

    text = output.decode("utf-8", "replace").replace("\r\n", "\n").strip()
    if timed_out:
        return _clip(f"Stopped after {timeout}s, with everything it started. "
                     f"Output so far:\n{text or '(none)'}")
    if not still_ours:
        return _clip(f"Killed by the user. Output so far:\n{text or '(none)'}")
    # Finished normally: let go without killing, so anything it deliberately
    # launched — an editor, a folder window — stays open.
    winjob.release_job(job)
    code = process.returncode
    status = "ok" if code == 0 else f"exit code {code}"
    return _clip(f"[{status}, in {folder}]\n{text or '(no output)'}")
