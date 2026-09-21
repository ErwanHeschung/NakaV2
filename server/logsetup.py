"""Where log lines go: a rotating naka.log, and the console when there is one.

naka.log used to exist only because a bash script redirected the server's
stdout into it. There is no such script on Windows, and once the tray starts
the server there is no console either — without a file of its own, the human
readable log would simply stop existing.

Rotation replaces day-based pruning for this one file. A log handler holds its
file open at a byte offset, so rewriting that file in place from another
thread — which the old pruner did, to cope with the shell redirect — would
leave the handler writing past the truncation, and on Windows the rename at
rollover would fail against a file someone else has open. Five files of 8 MB
is the retention policy for the text log now. retention_days still governs the
.jsonl records, which have their own timestamps and no open handle.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import paths

FORMAT = "%(asctime)s %(name)-12s %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 8_000_000
BACKUPS = 5


def configure(name: str = "naka.log", max_bytes: int = MAX_BYTES,
              backups: int = BACKUPS) -> RotatingFileHandler:
    paths.LOGS.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT, DATEFMT)

    file_handler = RotatingFileHandler(
        paths.LOGS / name, maxBytes=max_bytes, backupCount=backups,
        encoding="utf-8",
        # Not opened until the first line is written, so importing the server
        # to look at something does not create an empty log.
        delay=True,
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(file_handler)

    # The console only when a person is watching one. Under the tray, stderr
    # is a pipe into the supervisor, which keeps its own tail for crash reports.
    if sys.stderr is not None and sys.stderr.isatty():
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    # uvicorn logs through loggers that do not propagate to the root, so a
    # request that raised would print its traceback to a console that is not
    # there and leave naka.log none the wiser. Its error logger gets the file
    # too; its access log does not — the panel polls every two seconds, and a
    # line per poll would bury everything else.
    logging.getLogger("uvicorn.error").addHandler(file_handler)
    return file_handler
