from pathlib import Path
from psf_parser.parser import PsfParser

class PsfFile:
    """Class representing a parsed PSF (Parameter Storage Format) file.

    This class is responsible for loading, parsing, and storing the contents of a PSF file. It uses the
    PsfParser to parse the file and provides access to various parts of the parsed data, such as sweeps,
    traces, and values.

    Attributes:
        path: Path to the PSF file as a string.
        parser: An instance of the PsfParser used to parse the PSF file.
        header: Header information from the PSF file.
        sweeps: List of sweeps extracted from the PSF file.
        traces: List of traces extracted from the PSF file.
        values: List of values extracted from the PSF file.
    """

    def __init__(self, path: str | Path):
        """
        Initialize a PsfFile instance, parse the PSF file, and extract relevant data.

        Args:
            path: Path to the PSF file as a string or Path object. The path is passed to the PsfParser for parsing.
        """
        self.path = str(path)
        self.parser = PsfParser(path).parse()
        self.header = self.parser.header
        self.sweeps = self.parser.registry.sweeps
        self.traces = self.parser.registry.traces
        self.values = self.parser.registry.values

    @property
    def signals(self) -> list:
        """Retrieve a combined list of sweeps, traces, and values as 'signals'."""
        return self.sweeps + self.traces + self.values
