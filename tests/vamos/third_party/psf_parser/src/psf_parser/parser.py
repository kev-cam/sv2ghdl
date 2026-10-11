from __future__ import annotations
from typing import Optional, Literal
from pathlib import Path
from abc import ABC, abstractmethod

from psf_parser.registry import Registry


class PsfParser(ABC):
    """
    Base class for parsing PSF files.

    This class acts as a factory for creating specific parsers based on the file format (ASCII or binary).
    It also defines the common interface for parsing PSF files.

    Attributes:
        path: Path to the PSF file to parse.
        header: Dictionary to store the header information from the PSF file.
        registry: A Registry instance to manage parsed declarations.
    """

    def __init__(self, path: str | Path):
        self.path = path
        self.header: dict = {}  # Placeholder for header information
        self.registry = Registry()

    def __new__(cls, path: str | Path, format: Optional[Literal['ascii', 'binary']] = None) -> PsfParser:
        """
        Factory method that creates an instance of a specific parser subclass based on the file format.

        Args:
            path: Path to the PSF file to parse.
            format: The format of the PSF file. If None, the format is auto-detected.

        Returns:
            PsfParser: An instance of the appropriate subclass of PsfParser (either PsfAsciiParser or PsfBinParser).

        Raises:
            ValueError: If the format is unsupported or cannot be determined.
        """
        if cls is PsfParser:  # Only intercept direct calls to PsfParser
            if format is None:
                format = PsfParser.detect_format(path)
            if format == 'ascii':
                from psf_parser.ascii.parser import PsfAsciiParser
                return super().__new__(PsfAsciiParser)
            elif format == 'binary':
                from psf_parser.binary.parser import PsfBinParser
                return super().__new__(PsfBinParser)
            else:
                raise ValueError(f'Error: Unsupported format - {format}')
        return super().__new__(cls)

    @abstractmethod
    def parse(self) -> PsfParser:
        """
        Abstract method that should be implemented by subclasses to parse the PSF file.

        Returns:
            PsfParser: The parser instance.
        """
        pass

    @staticmethod
    def detect_format(path: str | Path) -> Literal['ascii', 'binary']:
        """
        Detect the format of the PSF file based on its contents.

        Args:
            path: Path to the PSF file to check.

        Returns:
            Literal['ascii', 'binary']: The detected format of the file.

        Notes:
            This method uses a very simple and naive check for the file header.
        """
        with open(path, 'rb') as file:
            if file.read(6) == b'HEADER':  # Simple header-based format detection
                return 'ascii'
            else:
                return 'binary'
