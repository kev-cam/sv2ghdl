from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Iterator, Optional, Union
from collections.abc import Iterable

SECTION_KEYWORDS = { 'HEADER', 'TYPE', 'SWEEP', 'TRACE', 'VALUE', 'END' }
TYPE_KEYWORDS = { 'STRING', 'INT', 'FLOAT', 'COMPLEX', 'ARRAY', 'STRUCT' }
SUBTYPE_KEYWORDS = { 'DOUBLE', 'LONG', 'BYTE' }
MISC_KEYWORDS = { 'GROUP', 'PROP' }
KEYWORDS = SECTION_KEYWORDS | TYPE_KEYWORDS | SUBTYPE_KEYWORDS | MISC_KEYWORDS


TOKEN_SPECIFICATION = [
    ('STRING', r'"(?:\\.|[^"\\])*"'),
    ('FLOAT', r'[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?|[+-]?\d+[eE][+-]?\d+|[+-]?(?:inf|nan)'),
    ('INT', r'[+-]?[0-9]+'),
    ('KW', r'[A-Z]+'),
    ('LPAREN', r'\('),
    ('RPAREN', r'\)'),
    ('SKIP', r'[\s*]+'),
    ('MISMATCH', r'.'),
]

TOKEN_REGEX = re.compile('|'.join(f'(?P<{name}>{pattern})' for name, pattern in TOKEN_SPECIFICATION))


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    value: Union[str, int, float]
    row: int
    column: int

    def matches(self, kinds: str | Iterable[str]) -> bool:
        return self.kind in kinds if isinstance(kinds, Iterable) else self.kind == kinds

    def expect(self, kinds: str | Iterable[str]) -> Token:
        if not self.matches(kinds):
            raise SyntaxError(f'Expected {kinds}, got {self}')
        return self


class Tokenizer:
    def __init__(self, text: str):
        self.text = text
        self.tokens: list[Token] = list(self._generate_tokens())
        self.position = 0

    def _generate_tokens(self) -> Iterator[Token]:
        row, col = 1, 1
        for match in TOKEN_REGEX.finditer(self.text):
            kind = match.lastgroup
            value = match.group(kind)
            token_len = len(value)

            match kind:
                case 'SKIP':
                    for char in value:
                        if char == '\n':
                            row += 1
                            col = 1
                        else:
                            col += 1
                    continue
                case 'MISMATCH':
                    raise SyntaxError(f"Unexpected character {value!r} at ({row}, {col})")
                case 'STRING':
                    value = value[1:-1]
                case 'FLOAT':
                    value = float(value)
                case 'INT':
                    value = int(value)
                case 'NAN':
                    kind = 'FLOAT'
                    value = float(value)
                case 'KW':
                    kind = 'KW_' + value

            yield Token(kind, value, row, col)
            col += token_len

    def goto(self, position: int = 0):
        self.position = position

    def has_next(self, n: int = 1) -> bool:
        return self.position + n <= len(self.tokens)

    def peek(self) -> Optional[Token]:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def next(self) -> Optional[Token]:
        token = self.peek()
        self.position += 1
        return token
