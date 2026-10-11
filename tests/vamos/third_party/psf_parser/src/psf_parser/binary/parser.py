from enum import Enum
import contextlib

from psf_parser.parser import PsfParser
from psf_parser.declaration import (Section, Datatype, TypeDeclaration, ArrayTypeDeclaration, StructTypeDeclaration, GroupDeclaration, DataDeclaration)
from psf_parser.binary.reader import BinaryReader
from psf_parser.binary.chunk_id import ChunkId


class ValueSectionType(Enum):
    NONE = 0
    SIMPLE = 512
    WINDOWED = 1024
    NON_SWEEP = 1280


class PsfBinParser(PsfParser):

    def __init__(self, path: str):
        super().__init__(path)
        self.scope_stack: list[str] = []

    def parse(self):
        with open(self.path, "rb") as f:
            self.reader = BinaryReader(f)

            if self.reader.is_seekable():
                self.validate_signature()
                self.reader.seek(0)

            self.value_section_type = ValueSectionType(self.reader.read_uint32())

            self.parse_header_section()

            if ChunkId(self.reader.peek_uint32()).matches(ChunkId.SECTION_TYPE):
                self.parse_type_section()

            if ChunkId(self.reader.peek_uint32()).matches(ChunkId.SECTION_SWEEP):
                self.parse_sweep_section()

            if ChunkId(self.reader.peek_uint32()).matches(ChunkId.SECTION_TRACE):
                self.parse_trace_section()

            if ChunkId(self.reader.peek_uint32()).matches(ChunkId.SECTION_VALUE):
                self.parse_value_section()

            self.reader.close()

        return self

    @contextlib.contextmanager
    def scoped(self, name: str):
        self.scope_stack.append(name)
        try:
            yield
        finally:
            self.scope_stack.pop()

    def read_container_preamble(self, chunk_id: ChunkId) -> int:
        ChunkId(self.reader.read_uint32()).expect(chunk_id)
        match chunk_id:
            case ChunkId.CONTAINER:
                return self.reader.read_uint32() - 4
            case ChunkId.SUBCONTAINER:
                return self.reader.read_uint32()

    def read_container_index(self, bytes_per_id: int):
        ChunkId(self.reader.read_uint32()).expect(ChunkId.CONTAINER_INDEX)
        index_size = self.reader.read_uint32()
        index = {}
        for _ in range(index_size // (4 + bytes_per_id)):
            decl_id = self.reader.read_uint32()
            index[decl_id] = self.reader.read_bytes(bytes_per_id)
        return index

    def check_container_end(self, endpos: int):
        if self.reader.tell() != endpos:
            raise ValueError(f"{self.reader.tell()}, {endpos}")

    def read_properties(self) -> dict:
        properties = {}
        while True:
            chunk_id = ChunkId(self.reader.peek_uint32())
            if not chunk_id.matches(ChunkId.properties()):
                break
            self.reader.skip(4)
            key = self.reader.read_string()
            match chunk_id:
                case ChunkId.PROP_STRING:
                    value = self.reader.read_string()
                case ChunkId.PROP_INT:
                    value = self.reader.read_uint32()
                case ChunkId.PROP_FLOAT:
                    value = self.reader.read_float64()
            properties[key] = value
        return properties

    def read_type_declaration(self) -> int:
        ChunkId(self.reader.read_uint32()).expect(ChunkId.DECLARATION)
        decl_id = self.reader.read_uint32()
        name = self.reader.read_string()
        arraytype = Datatype(self.reader.read_uint32())
        datatype = Datatype(self.reader.read_uint32())
        match datatype:
            case dt if dt in {
                Datatype.INT8,
                Datatype.STRING,
                Datatype.INT32,
                Datatype.FLOAT64,
                Datatype.COMPLEX,
            }:
                decl = TypeDeclaration(decl_id, name, Section.TYPE, datatype)
            case Datatype.ARRAY:
                decl = ArrayTypeDeclaration(decl_id, name, Section.TYPE, datatype, arraytype)
            case Datatype.STRUCT:
                decl = StructTypeDeclaration(decl_id, name, Section.TYPE, datatype)
                with self.scoped(name):
                    while not ChunkId(self.reader.peek_uint32()).matches(ChunkId.STRUCT_END):
                        member_id = self.read_type_declaration()
                        decl.members.append(member_id)
                self.reader.skip(4)
        decl.properties = self.read_properties()
        self.registry.add(decl, scope=tuple(self.scope_stack))
        return decl_id

    def read_data_declaration(self, section: Section) -> int:
        chunk_id = ChunkId(self.reader.read_uint32()).expect({ChunkId.DECLARATION, ChunkId.GROUP_DECLARATION})
        decl_id = self.reader.read_uint32()
        name = self.reader.read_string()
        match chunk_id:
            case ChunkId.GROUP_DECLARATION:
                group_size = self.reader.read_uint32()
                decl = GroupDeclaration(decl_id, name, section)
                with self.scoped(name):
                    for _ in range(group_size):
                        member_id = self.read_data_declaration(section)
                        decl.members.append(member_id)
            case ChunkId.DECLARATION:
                type_id = self.reader.read_uint32()
                decl = DataDeclaration(decl_id, name, section, type_id)
                if section is Section.VALUE:
                    decl.data = self.read_data(self.registry.get_by_id(decl.type_id))
                else:
                    decl.data = []
        self.registry.add(decl, scope=tuple(self.scope_stack))
        decl.properties = self.read_properties()
        return decl_id

    def read_data(self, type_decl: TypeDeclaration):
        match type_decl.datatype:
            case Datatype.INT8:
                val = self.reader.read_uint8()
                self.reader.skip(3)
                return val
            case Datatype.STRING:
                return self.reader.read_string()
            case Datatype.INT32:
                return self.reader.read_uint32()
            case Datatype.FLOAT64:
                return self.reader.read_float64()
            case Datatype.COMPLEX:
                return complex(self.reader.read_float64(), self.reader.read_float64())
            case Datatype.ARRAY:
                raise NotImplementedError()
            case Datatype.STRUCT:
                result = {}
                for member_id in type_decl.members:
                    member = self.registry.get_by_id(member_id)
                    result[member.name] = self.read_data(member)
                return result

    def validate_signature(self):
        self.reader.seek(-12, 2)
        magic_number = self.reader.read_bytes(8).decode()
        if magic_number != "Clarissa":
            raise SyntaxError("Footer magic number incorrect")

    def parse_header_section(self):
        endpos = self.read_container_preamble(ChunkId.CONTAINER)
        self.header = self.read_properties()
        self.check_container_end(endpos)

    def parse_type_section(self):
        ChunkId(self.reader.read_uint32()).expect(ChunkId.SECTION_TYPE)
        endpos = self.read_container_preamble(ChunkId.CONTAINER)
        sub_endpos = self.read_container_preamble(ChunkId.SUBCONTAINER)
        while self.reader.tell() < sub_endpos:
            self.read_type_declaration()
        self.read_container_index(bytes_per_id=4)
        self.check_container_end(endpos)

    def parse_sweep_section(self):
        ChunkId(self.reader.read_uint32()).expect(ChunkId.SECTION_SWEEP)
        endpos = self.read_container_preamble(ChunkId.CONTAINER)
        while self.reader.tell() < endpos:
            self.read_data_declaration(Section.SWEEP)
        self.check_container_end(endpos)

    def parse_trace_section(self):
        ChunkId(self.reader.read_uint32()).expect(ChunkId.SECTION_TRACE)
        endpos = self.read_container_preamble(ChunkId.CONTAINER)
        sub_endpos = self.read_container_preamble(ChunkId.SUBCONTAINER)
        while self.reader.tell() < sub_endpos:
            self.read_data_declaration(Section.TRACE)
        self.read_container_index(bytes_per_id=12)
        self.check_container_end(endpos)

    def parse_value_section(self):
        ChunkId(self.reader.read_uint32()).expect(ChunkId.SECTION_VALUE)
        endpos = self.read_container_preamble(ChunkId.CONTAINER)

        match self.value_section_type:
            case ValueSectionType.NONE:
                pass
            case ValueSectionType.NON_SWEEP:
                sub_endpos = self.read_container_preamble(ChunkId.SUBCONTAINER)
                while self.reader.tell() < sub_endpos:
                    self.read_data_declaration(Section.VALUE)
                self.read_container_index(bytes_per_id=4)

            case ValueSectionType.SIMPLE:
                while self.reader.tell() < endpos:
                    ChunkId(self.reader.read_uint32()).expect(ChunkId.DECLARATION)
                    decl_id = self.reader.read_uint32()
                    decl = self.registry.get_by_id(decl_id)
                    decl.data.append(
                        self.read_data(self.registry.get_by_id(decl.type_id))
                    )

            case ValueSectionType.WINDOWED:
                sweep_decls = self.registry.sweeps
                if len(sweep_decls) != 1:
                    raise SyntaxError("Error: Expected exactly one sweep declaration for WINDOWED value section.")

                time_decl = sweep_decls[0]
                data_decls = [time_decl] + self.registry.traces

                while True:
                    while self.reader.tell() < endpos and ChunkId(self.reader.peek_uint32()).matches(ChunkId.CONTAINER_PADDING):
                        self.reader.skip(4)
                        padding_size = self.reader.read_uint32()
                        self.reader.skip(padding_size)

                    if self.reader.tell() >= endpos:
                        break

                    ChunkId(self.reader.read_uint32()).expect(ChunkId.DECLARATION)
                    window_size = self.reader.read_uint16()
                    num_words = self.reader.read_uint16()

                    for i, decl in enumerate(data_decls):
                        for _ in range(num_words):
                            decl.data.append(self.reader.read_float64())
                        self.reader.skip((window_size - num_words) * 8)
                        if i < len(data_decls) - 1:
                            self.reader.skip(8)

        self.check_container_end(endpos)
        ChunkId(self.reader.read_uint32()).expect(ChunkId.SECTION_END)
