"""Output writers for the spectre personality: PSF ASCII, nutmeg, state files.

See docs/VAMOS_SPECTRE_DESIGN.md §8 (formats) and §10 (APIs).  The writers stream
AnalysisResult objects (vamos.spectre.results); none of them reads the IR.  Standard
library only; Python 3.9.
"""
