"""The Job IR: what a simulator command line asked for, tool-neutral.

Every personality turns its argv into a Job; backends only ever read Jobs.
A Job is serialised to <daidir>/vamos.job.json so a later step (the generated
./simv, or the three-step flow) can pick up where the compile left off.
"""

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Dispositions for options that were accepted but not (fully) acted on.
IGNORED = "ignored"          # meaningless here (e.g. -full64); silent
NOTED = "noted"              # accepted, one-line note to the user
UNSUPPORTED = "unsupported"  # accepted, warned, logged
UNKNOWN = "unknown"          # not in the option table at all


@dataclass
class Source:
    path: str                # absolute
    lang: str                # verilog | sv | vhdl | vams | spice | c
    library: str = "work"


@dataclass
class Unmapped:
    option: str
    disposition: str
    note: str = ""


@dataclass
class Job:
    personality: str
    argv: List[str] = field(default_factory=list)
    cwd: str = ""

    sources: List[Source] = field(default_factory=list)
    lib_files: List[str] = field(default_factory=list)     # -v
    lib_dirs: List[str] = field(default_factory=list)      # -y
    lib_exts: List[str] = field(default_factory=list)      # +libext+
    incdirs: List[str] = field(default_factory=list)
    defines: Dict[str, Optional[str]] = field(default_factory=dict)
    timescale: Optional[str] = None
    std: str = "verilog"                                   # verilog | sv
    tops: List[str] = field(default_factory=list)

    # outputs
    exe: str = "simv"
    daidir: str = ""
    mdir: str = "csrc"
    log: Optional[str] = None

    # stages this invocation performs
    analyse: bool = True
    elaborate: bool = True
    run: bool = False

    # runtime
    plusargs: List[str] = field(default_factory=list)
    run_log: Optional[str] = None
    seed: Optional[str] = None

    debug: List[str] = field(default_factory=list)
    coverage: List[str] = field(default_factory=list)
    unmapped: List[Unmapped] = field(default_factory=list)

    def note(self, option: str, disposition: str, note: str = "") -> None:
        self.unmapped.append(Unmapped(option, disposition, note))

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Job":
        d = json.loads(text)
        d["sources"] = [Source(**s) for s in d.get("sources", [])]
        d["unmapped"] = [Unmapped(**u) for u in d.get("unmapped", [])]
        return cls(**d)
