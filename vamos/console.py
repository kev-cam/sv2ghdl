"""stdout/stderr with an optional log file (-l), as vendor tools do."""

import sys
from typing import Optional, TextIO


class Console:
    def __init__(self) -> None:
        self.log: Optional[TextIO] = None

    def open_log(self, path: str, append: bool = False) -> None:
        self.close()
        self.log = open(path, "a" if append else "w", encoding="utf-8")

    def out(self, line: str) -> None:
        print(line, flush=True)
        if self.log:
            self.log.write(line + "\n")
            self.log.flush()

    def err(self, line: str) -> None:
        print(line, file=sys.stderr, flush=True)
        if self.log:
            self.log.write(line + "\n")
            self.log.flush()

    def close(self) -> None:
        if self.log:
            self.log.close()
            self.log = None
