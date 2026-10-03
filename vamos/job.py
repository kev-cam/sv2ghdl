"""The Job IR: what a simulator command line asked for, tool-neutral.

Every personality turns its argv into a Job; backends only ever read Jobs.
A Job is serialised to <daidir>/vamos.job.json so a later step (the generated
./simv, or the three-step flow) can pick up where the compile left off.
"""

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Version of the vamos.job.json layout.  from_json refuses anything newer, or
# any key it does not know, so a daidir from a newer vamos says "recompile"
# instead of silently running without what the newer vamos recorded.
SCHEMA = 2


class JobVersionError(ValueError):
    pass


# Dispositions for options that were accepted but not (fully) acted on.
IGNORED = "ignored"          # meaningless here (e.g. -full64); silent
NOTED = "noted"              # accepted, one-line note to the user
UNSUPPORTED = "unsupported"  # accepted, warned, logged
UNKNOWN = "unknown"          # not in the option table at all
INAPPLICABLE = "inapplicable"  # a vamos option that has no effect in this invocation; warned


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
    timescale: Optional[str] = None                        # -timescale (prelude only)
    override_timescale: Optional[str] = None               # -override_timescale (rewrites every directive)
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
    finish: Optional[str] = None         # simv +vcs+finish+N (N units of job.precision)
    seed: Optional[str] = None

    debug: List[str] = field(default_factory=list)
    coverage: List[str] = field(default_factory=list)
    unmapped: List[Unmapped] = field(default_factory=list)

    # AMS (vcs-ams).  ams_control is the parse-time request (-ad/+ad/vcs-ams:
    # the control file, "" = vcsAD.init); ams is the compile-to-run record,
    # written only when the AMS compile finishes (docs/VAMOS_AMS_DESIGN.md §1.1).
    ams_control: Optional[str] = None
    ams: Optional[dict] = None
    precision: Optional[str] = None      # simulation precision, when the preprocess step ran

    schema: int = SCHEMA

    def note(self, option: str, disposition: str, note: str = "") -> None:
        self.unmapped.append(Unmapped(option, disposition, note))

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Job":
        """Rebuild a Job.  A daidir written by a newer vamos (a newer schema,
        or keys this vamos does not know) raises JobVersionError."""
        d = json.loads(text)
        schema = d.get("schema", 1)
        names = {f.name for f in dataclasses.fields(cls)}
        unknown = sorted(k for k in d if k not in names)
        if schema > SCHEMA or unknown:
            raise JobVersionError("compiled by a newer vamos (schema %s%s); recompile"
                                  % (schema, ", unknown " + ", ".join(unknown) if unknown else ""))
        d["sources"] = [Source(**s) for s in d.get("sources", [])]
        d["unmapped"] = [Unmapped(**u) for u in d.get("unmapped", [])]
        return cls(**d)
