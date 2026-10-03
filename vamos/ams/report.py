"""The interface-element report (docs/VAMOS_AMS_DESIGN.md §3.6).

<exe>.msv/interface_element.rpt has one entry per analog node, in plan order.
Its control-file lines come from rules.ie_lines (rules.paste_line is the one
writer), so those lines pasted into vcsAD.init in place of its a2d/d2a/
map_by_node rules select the same nodes and reproduce their levels exactly,
supply nets included.  Comment lines after each entry say where the node goes
and where its levels came from.

The file is UTF-8 whatever the locale (the direction comments carry '→'; a
reader should open it with encoding="utf-8"), and is written through a .tmp
file and os.replace; a failed write leaves no .tmp and raises ReportError.
"""

from __future__ import annotations

import os
from typing import List, Sequence

from vamos.ams import rules
from vamos.ams.model import (A2D, BIDIR, D2A, DISABLED, NONE, POWERNET, RA2D, RD2A, REMOVED,
                             THROUGH, AmsPlan, AnalogNode)
from vamos.backends.nvc import BackendError
from vamos.netlist.numbers import fmt
from vamos.notes import NoteError, error

ENCODING = "utf-8"


class ReportError(NoteError, BackendError):
    """The report could not be written.  A NoteError (one error note naming the file),
    so a stage runner prints it like any other stage's notes, and a BackendError, so
    vcs.main reports it as "vamos: error: ..." when nothing above catches it."""

    def __str__(self) -> str:
        return "; ".join(("%s: %s" % (n.origin, n.message)) if n.origin else n.message
                         for n in self.notes)


def _boundary_nets(plan: AmsPlan, node: AnalogNode) -> List[str]:
    out = []
    for ref in node.ports:
        inst = plan.analysis.instances[ref.inst]
        sp = inst.spice.get((ref.port, ref.bit))
        if sp is None:
            cell = plan.analysis.cells[inst.cell]
            sp = cell.ports[ref.port].verilog + ("" if ref.bit == 0 else "[%d]" % ref.bit)
        out.append("%s.%s" % (inst.vpath, sp))
    return out


def _top_net(node: AnalogNode) -> str:
    """The highest alias, the one with the fewest '.' separators (PAMS p177).

    A node is one net bit: the bare parent-bus alias that §3.1 keeps for whole-bus
    rules (`tb.arr` beside `tb.arr[0]`) names the whole bus, not this node, so it is
    dropped whenever its bit alias is present (VCS prints `top.s[0]`, PAMS p270)."""
    cands = [a for a in node.aliases if a] or [node.canonical]
    buses = {a.rsplit("[", 1)[0] for a in cands if a.endswith("]")}
    named = [a for a in cands if a not in buses] or cands
    return min(named, key=lambda a: (a.count("."), named.index(a)))


def _open_lines(opens: Sequence) -> List[str]:
    """`port_connect ... => snps_open` ports: the private node and its shunt (§3.6, §4.7)."""
    out: List[str] = []
    for o in opens:
        out += ["// node=%s.%s: snps_open (port_connect), private node %s" % (o.vpath, o.port, o.node),
                "// shunt %s 1e12 ohm to ground" % o.shunt, ""]
    return out


def text(plan: AmsPlan, engine: str, opens: Sequence = ()) -> str:
    """The report.  opens: deck.OpenPort records (vpath, port, node, shunt) of the SPICE
    ports tied off with snps_open, which are no plan node but get a node and a shunt."""
    lines = ["// vamos interface-element report (%s). Entries are control-file syntax: a line"
             % engine,
             "// pasted into vcsAD.init selects the same node.", ""]
    for node in plan.nodes:
        entry: List[str] = rules.ie_lines(node.role, node.canonical, node.d2a, node.a2d)
        if node.role == POWERNET and node.dc is not None:
            entry.append("// node=%s: supply net, ideal %s V source (powernet)"
                         % (node.canonical, fmt(node.dc)))
        elif node.role == REMOVED:
            entry.append("// node=%s: remove_d2a%s" % (
                node.canonical, " dc=%s" % fmt(node.dc) if node.dc is not None else
                " (left to the analog side)"))
        elif node.role == DISABLED:
            entry.append("// node=%s: disable_ie (no interface element)" % node.canonical)
        elif node.role in (RD2A, RA2D):
            entry.append("// node=%s: real port, %s" % (
                node.canonical, "ideal source from the digital value" if node.role == RD2A
                else "node voltage deposited into the digital"))
        elif node.role == THROUGH:
            entry.append("// node=%s: through-net (analog only, no interface element)"
                         % node.canonical)
        elif node.role == NONE:
            entry.append("// node=%s: no interface element (%s)" % (
                node.canonical, "unconnected" if len(node.ports) <= 1 else "not driven"))
        if not entry:
            continue
        lines += entry
        if node.role in (D2A, A2D, BIDIR, RD2A, RA2D):
            lines.append("// Top-Net %s" % _top_net(node))
            lines.append("// All Boundary Nets %s" % " ".join(_boundary_nets(plan, node)))
        lines += ["// " + r if not r.startswith("//") else r for r in node.report]
        if node.shunt:
            lines.append("// shunt rsh_%s 1e12 ohm to ground" % node.name)
        lines.append("")
    lines += _open_lines(opens)
    return "\n".join(lines)


def write(path: str, plan: AmsPlan, engine: str, opens: Sequence = ()) -> None:
    """Write the report as UTF-8, whatever the locale's encoding (its comments carry
    '→', which a latin-1 locale cannot encode).  Raises ReportError naming the file
    when it cannot be written; no .tmp file is left behind."""
    body = text(plan, engine, opens)
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # backslashreplace: a lone surrogate (an undecodable byte of a file name in an
        # origin) is written as \udcXX instead of failing the compile; the file stays UTF-8
        with open(tmp, "w", encoding=ENCODING, errors="backslashreplace") as fh:
            fh.write(body)
        os.replace(tmp, path)
    except (OSError, UnicodeError) as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        why = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
        raise ReportError([error(path, "cannot write the interface-element report: %s" % why)])
