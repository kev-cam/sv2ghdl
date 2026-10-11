"""Expression AST (frozen contract, docs/VAMOS_AMS_DESIGN.md §4.1).

expr.parse() builds these; expr.evaluate() and the printers consume them.
Nodes are immutable and hashable so they can be shared between scopes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple, Union


@dataclass(frozen=True)
class Num:
    value: float


@dataclass(frozen=True)
class Name:
    name: str            # lowercased identifier (parameter, temper, time, ...)


@dataclass(frozen=True)
class Str:
    text: str            # string literal, without quotes


@dataclass(frozen=True)
class Call:
    func: str            # lowercased function name: pow, v, i, if, ...
    args: Tuple["Expr", ...]


@dataclass(frozen=True)
class Unary:
    op: str              # '-', '+', '!', '~'
    arg: "Expr"


@dataclass(frozen=True)
class Binary:
    op: str              # + - * / ^ ** == != < <= > >= && || & | << >> ~^
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Ternary:
    cond: "Expr"
    a: "Expr"
    b: "Expr"


Expr = Union[Num, Name, Str, Call, Unary, Binary, Ternary]
