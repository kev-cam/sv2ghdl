from __future__ import annotations
from collections.abc import Iterable
from enum import Enum

class ChunkId(Enum):
    SECTION_HEADER = 0
    SECTION_TYPE = 1
    SECTION_SWEEP = 2
    SECTION_TRACE = 3
    SECTION_VALUE = 4
    SECTION_END = 15

    DECLARATION = 16
    GROUP_DECLARATION = 17
    STRUCT_END = 18

    CONTAINER_INDEX = 19
    CONTAINER_PADDING = 20
    CONTAINER = 21
    SUBCONTAINER = 22

    PROP_STRING = 33
    PROP_INT = 34
    PROP_FLOAT = 35

    @classmethod
    def _missing_(cls, value):
        raise SyntaxError(f'Error: No ChunkId matching {value}.')

    @classmethod
    def sections(cls):
        return {cls.SECTION_HEADER, cls.SECTION_TYPE, cls.SECTION_SWEEP, cls.SECTION_TRACE, cls.SECTION_VALUE}

    @classmethod
    def properties(cls):
        return {cls.PROP_STRING, cls.PROP_INT, cls.PROP_FLOAT}

    def matches(self, ids: Iterable[ChunkId] | ChunkId):
        if isinstance(ids, Iterable):
             return self in ids
        else:
            return self == ids

    def expect(self, ids: Iterable[ChunkId] | ChunkId):
        if not self.matches(ids):
            raise SyntaxError(f'Error: Got {self} but expected {ids}')
        return self
