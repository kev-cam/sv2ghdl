"""Banner profiles and the tool/licence provenance header (VAMOS_PLAN 4b).

Everything user-visible that a vendor tool would also print comes from a
banner profile: a JSON file of templates, one per personality.  The shipped
defaults brand the text "vamos"; a user profile can match a vendor layout for
log-scraping scripts.  Profiles only control format - there is no slot for
vendor licence or copyright text.

The provenance header lists the tools actually used, each with its own
licence, and is printed whatever profile is selected.
"""

import json
import os
from typing import Dict, List, Optional, Tuple

from vamos import tools

_PKG = os.path.dirname(os.path.abspath(__file__))


def _config_layers() -> List[str]:
    """Config dirs, lowest precedence first."""
    layers = [_PKG]
    root = tools.package_root()
    # installed: PREFIX/lib -> PREFIX/etc/vamos
    layers.append(os.path.join(os.path.dirname(root), "etc", "vamos"))
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    layers.append(os.path.join(xdg, "vamos"))
    layers.append(os.path.join(os.path.expanduser("~"), ".vamos"))
    layers.append(os.path.join(os.getcwd(), ".vamos"))
    return layers


def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load_profile(personality: str, choice: Optional[str] = None) -> Optional[dict]:
    """Return the banner profile dict, or None for 'none'.

    choice: --vamos-banner value (a name or a path); falls back to
    $VAMOS_BANNER, then the personality's own name.
    """
    choice = choice or os.environ.get("VAMOS_BANNER") or personality
    if choice == "none":
        return None
    if os.sep in choice or choice.endswith(".json"):
        prof = _load_json(choice)
        if prof is None:
            raise ValueError("cannot read banner profile '%s'" % choice)
        return prof
    found = None
    for layer in _config_layers():
        p = _load_json(os.path.join(layer, "banners", choice + ".json"))
        if p is not None:
            found = p           # later layers win
    if found is None and choice != personality:
        raise ValueError("no banner profile named '%s'" % choice)
    return found or {}


class Banner:
    def __init__(self, profile: Optional[dict], personality: str):
        self.profile = profile
        self.personality = personality

    def text(self, key: str, **fields) -> Optional[str]:
        if not self.profile or key not in self.profile:
            return None
        brand = self.profile.get("brand", "vamos")
        from vamos import VERSION
        vals = dict(brand=brand.lower(), Brand=brand[:1].upper() + brand[1:].lower(),
                    BRAND=brand.upper(), BRAND_SPACED=" ".join(brand.upper()),
                    version=VERSION, personality=self.personality)
        vals.update(fields)
        try:
            return self.profile[key].format(**vals)
        except (KeyError, IndexError, ValueError) as e:
            return "vamos: banner template '%s' is invalid: %s" % (key, e)


def licenses() -> Dict[str, dict]:
    table: Dict[str, dict] = {}
    for layer in _config_layers():
        d = _load_json(os.path.join(layer, "licenses.json"))
        if d:
            for k, v in d.items():
                if not k.startswith("_"):
                    table[k] = v
    return table


def provenance(used: List[Tuple[str, str, str]], personality: str) -> str:
    """used: [(tool, version, path)] -> the 'tools used' header.  The columns are as wide
    as their longest entry, so a long version or licence never shifts the next column."""
    from vamos import VERSION
    lic = licenses()
    rows = [(tool, ver or "?", lic.get(tool, {}).get("spdx", "licence unknown"), path)
            for tool, ver, path in used]
    wt = max([len(r[0]) for r in rows] + [8])
    wv = max([len(r[1]) for r in rows] + [14])
    ws = max([len(r[2]) for r in rows] + [26])
    lines = ["vamos %s (%s personality) - tools used:" % (VERSION, personality)]
    for tool, ver, spdx, path in rows:
        lines.append("  %-*s  %-*s %-*s %s" % (wt, tool, wv, ver, ws, spdx, path))
    return "\n".join(lines)


def license_report() -> str:
    """vamos --vamos-licenses: every tool in the licence table, its licence, the source
    the stack runs and, for a fork, the project it forks."""
    lic = licenses()
    names = sorted(lic, key=str.lower)
    wt = max([len(t) for t in names] + [10])
    ws = max([len(lic[t].get("spdx", "?")) for t in names] + [20])
    lines = ["Tools vamos may run, and their licences:"]
    for tool in names:
        e = lic[tool]
        line = "  %-*s %-*s %s" % (wt, tool, ws, e.get("spdx", "?"), e.get("url", ""))
        if e.get("upstream"):
            line += " (a fork of %s)" % e["upstream"]
        lines.append(line.rstrip())
    lines.append("")
    lines.append("vamos never reproduces vendor licence or copyright text.")
    return "\n".join(lines)
