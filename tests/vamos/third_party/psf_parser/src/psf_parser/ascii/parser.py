import contextlib
from pathlib import Path

from psf_parser.parser import PsfParser
from psf_parser.declaration import (Section, Datatype, TypeDeclaration, ArrayTypeDeclaration, StructTypeDeclaration, GroupDeclaration, DataDeclaration)
from psf_parser.ascii.token import Tokenizer


class PsfAsciiParser(PsfParser):

    def __init__(self, path: str):
        super().__init__(path)
        self.scope_stack: list[str] = []

    def parse(self):
        text = Path(self.path).read_text()
        self.tokenizer = Tokenizer(text)

        self.parse_header_section()

        if self.tokenizer.peek().matches('KW_TYPE'):
            self.parse_type_section()

        if self.tokenizer.peek().matches('KW_SWEEP'):
            self.parse_sweep_section()

        if self.tokenizer.peek().matches('KW_TRACE'):
            self.parse_trace_section()

        if self.tokenizer.peek().matches('KW_VALUE'):
            self.parse_value_section()

        return self


    @contextlib.contextmanager
    def scoped(self, name: str):
        self.scope_stack.append(name)
        try:
            yield
        finally:
            self.scope_stack.pop()


    def read_properties(self) -> dict:
        properties = {}
        if not self.tokenizer.peek().matches('KW_PROP'):
            return properties
        self.tokenizer.next().expect('KW_PROP')
        self.tokenizer.next().expect('LPAREN')
        while not self.tokenizer.peek().matches('RPAREN'):
            key_token = self.tokenizer.next().expect('STRING')
            value_token = self.tokenizer.next().expect({'STRING', 'INT', 'FLOAT'})
            properties[key_token.value] = value_token.value
        self.tokenizer.next()
        return properties


    def read_datatype(self) -> Datatype:
        type_map = {
            'KW_STRING': Datatype.STRING,
            'KW_ARRAY': Datatype.ARRAY,
            'KW_STRUCT': Datatype.STRUCT,
            'KW_INT': {
                'KW_LONG': Datatype.INT32,
                'KW_BYTE': Datatype.INT8,
            },
            'KW_FLOAT': {
                'KW_DOUBLE': Datatype.FLOAT64,
            },
            'KW_COMPLEX': {
                'KW_DOUBLE': Datatype.COMPLEX,
            },
        }
        type_kw = self.tokenizer.next().expect({'KW_STRING', 'KW_INT', 'KW_FLOAT', 'KW_COMPLEX', 'KW_ARRAY', 'KW_STRUCT'}).kind
        entry = type_map.get(type_kw)
        if isinstance(entry, Datatype):
            return entry
        else:
            subtype_kw = self.tokenizer.next().expect({'KW_DOUBLE', 'KW_LONG', 'KW_BYTE'}).kind
            try:
                return entry[subtype_kw]
            except KeyError:
                raise SyntaxError(f"Unknown subtype for {type_kw}: {subtype_kw!r}")


    def read_type_declaration(self, section: Section):
        decl_id = self.registry.generate_id()
        name = self.tokenizer.next().expect('STRING').value
        datatype = self.read_datatype()

        match datatype:
            case dt if dt in {Datatype.INT8, Datatype.STRING, Datatype.INT32, Datatype.FLOAT64, Datatype.COMPLEX,}:
                decl = TypeDeclaration(decl_id, name, section, datatype)

            case Datatype.ARRAY:
                self.tokenizer.next().expect('LPAREN')
                self.tokenizer.next().expect('RPAREN')
                arraytype = self.read_datatype()
                decl = ArrayTypeDeclaration(decl_id, name, section, datatype, arraytype)

            case Datatype.STRUCT:
                decl = StructTypeDeclaration(decl_id, name, section, datatype)
                self.tokenizer.next().expect('LPAREN')
                with self.scoped(name):
                    while not self.tokenizer.peek().matches('RPAREN'):
                        member_id = self.read_type_declaration(section)
                        decl.members.append(member_id)
                    self.tokenizer.next().expect('RPAREN')

        self.registry.add(decl, scope=tuple(self.scope_stack))
        decl.properties = self.read_properties()
        return decl_id


    def read_data_declaration(self, section: Section):
        decl_id = self.registry.generate_id()
        name = self.tokenizer.next().expect('STRING').value

        if self.tokenizer.peek().matches('KW_GROUP'):
            self.tokenizer.next()
            size_token = self.tokenizer.next().expect('INT')
            decl = GroupDeclaration(decl_id, name, section)
            with self.scoped(name):
                for _ in range(size_token.value):
                    decl.members.append(self.read_data_declaration())

        else:
            type_name = self.tokenizer.next().expect('STRING').value
            type_id = self.registry.get_by_name(type_name, Section.TYPE, scope=tuple(self.scope_stack)).id
            decl = DataDeclaration(decl_id, name, section, type_id)
            if section is Section.VALUE:
                decl.data = self.read_data_for_type_decl(self.registry.get_by_id(decl.type_id))
            else:
                decl.data = []

        self.registry.add(decl, scope=tuple(self.scope_stack))
        decl.properties = self.read_properties()
        return decl_id


    def read_data_for_type_decl(self, type_decl: TypeDeclaration):
        match type_decl.datatype:
            case dt if dt in {Datatype.INT8, Datatype.STRING, Datatype.INT32, Datatype.FLOAT64, Datatype.COMPLEX}:
                return self.read_data_for_datatype(type_decl.datatype)
            case Datatype.ARRAY:
                result = []
                self.tokenizer.next().expect('LPAREN')
                while not self.tokenizer.peek().matches('RPAREN'):
                    result.append(self.read_data_for_datatype(type_decl.arraytype))
                self.tokenizer.next().expect('RPAREN')
                return result
            case Datatype.STRUCT:
                result = {}
                self.tokenizer.next().expect('LPAREN')
                for member_id in type_decl.members:
                    member = self.registry.get_by_id(member_id)
                    result[member.name] = self.read_data_for_type_decl(member)
                self.tokenizer.next().expect('RPAREN')
                return result


    def read_data_for_datatype(self, datatype: Datatype):
        match datatype:
            case Datatype.INT8:
                return self.tokenizer.next().expect('INT').value
            case Datatype.STRING:
                return self.tokenizer.next().expect('STRING').value
            case Datatype.INT32:
                return self.tokenizer.next().expect('INT').value
            case Datatype.FLOAT64:
                return self.tokenizer.next().expect('FLOAT').value
            case Datatype.COMPLEX:
                self.tokenizer.next().expect('LPAREN')
                real = self.tokenizer.next().expect('FLOAT').value
                imag = self.tokenizer.next().expect('FLOAT').value
                self.tokenizer.next().expect('RPAREN')
                return complex(real, imag)


    def parse_header_section(self):
        self.tokenizer.next().expect('KW_HEADER')
        while self.tokenizer.has_next():
            if self.tokenizer.peek().matches({'KW_TYPE', 'KW_SWEEP', 'KW_TRACE', 'KW_VALUE', 'KW_END'}):
                break
            key_token = self.tokenizer.next().expect('STRING')
            value_token = self.tokenizer.next().expect({'STRING', 'INT', 'FLOAT'})
            self.header[key_token.value] = value_token.value
        else:
            raise SyntaxError('Unexpected end of HEADER section.')


    def parse_type_section(self):
        self.tokenizer.next().expect('KW_TYPE')
        while self.tokenizer.has_next():
            if self.tokenizer.peek().matches({'KW_SWEEP', 'KW_TRACE', 'KW_VALUE', 'KW_END'}):
                break
            self.read_type_declaration(Section.TYPE)


    def parse_sweep_section(self):
        self.tokenizer.next().expect('KW_SWEEP')
        while self.tokenizer.has_next():
            if self.tokenizer.peek().matches({'KW_TRACE', 'KW_VALUE', 'KW_END'}):
                break
            self.read_data_declaration(Section.SWEEP)


    def parse_trace_section(self):
        self.tokenizer.next().expect('KW_TRACE')
        while self.tokenizer.has_next():
            if self.tokenizer.peek().matches({'KW_VALUE', 'KW_END'}):
                break
            self.read_data_declaration(Section.TRACE)


    def parse_value_section(self):
        self.tokenizer.next().expect('KW_VALUE')

        if len(self.registry.sweeps) > 0:
            while self.tokenizer.has_next():
                if self.tokenizer.peek().matches({'KW_END'}):
                    break
                name = self.tokenizer.next().expect('STRING').value
                decl = self.registry.get_by_flat_name(name)[0]  # Safe since for SIMPLE value-sections, there should not be naming conflicts
                if isinstance(decl, GroupDeclaration):
                    for member_id in decl.members:
                        member_decl = self.registry.get_by_id(member_id)
                        member_decl.data.append(
                            self.read_data_for_type_decl(self.registry.get_by_id(member_decl.type_id))
                        )
                else:
                    decl.data.append(
                        self.read_data_for_type_decl(self.registry.get_by_id(decl.type_id))
                    )
        else:
            while self.tokenizer.has_next():
                if self.tokenizer.peek().matches({'KW_END'}):
                    break
                self.read_data_declaration(Section.VALUE)
