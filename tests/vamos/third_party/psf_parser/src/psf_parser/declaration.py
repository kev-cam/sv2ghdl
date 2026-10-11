from __future__ import annotations
from abc import ABC
from enum import Enum
from collections.abc import Sequence, Mapping
from typing import Optional, Union

DeclarationId = int
"""Unique identifiers for a declaration."""

DataUnit = Union[int, float, complex, str]
"""Smallest unit of data."""

Data = Union[
    DataUnit,
    Sequence[DataUnit],
    Mapping[str, DataUnit],
]
"""Data belonging to a DataDeclaration."""

Properties = Mapping[str, DataUnit]
"""Properties are key-value pairs."""


class Section(Enum):
    """Describes sections in a file."""
    HEADER = 'header'
    TYPE = 'type'
    SWEEP = 'sweep'
    TRACE = 'trace'
    VALUE = 'value'


class Datatype(Enum):
    """Describes datatypes for DataDeclarations."""
    NONE = 0
    INT8 = 1
    STRING = 2
    ARRAY = 3
    INT32 = 5
    FLOAT64 = 11
    COMPLEX = 12
    STRUCT = 16


class Declaration(ABC):
    """Abstract base class defining common declaration properties.

    Attributes:
        id: Unique identifier of this declaration.
        name: Name of this declaration.
        section: Section this declaration belongs to.
        properties: Additional properties.
    """

    def __init__(
        self,
        id: DeclarationId,
        name: str,
        section: Section,
        properties: Optional[Properties] = None
    ):
        self.id = id
        self.name = name
        self.section = section
        self.properties = properties if properties is not None else {}

    def __repr__(self):
        return f"<{self.__class__.__name__}: id={self.id}, name={self.name}>"


class GroupDeclaration(Declaration):
    """Declaration used to hold other (member) declarations.

    Attributes:
        id: Unique identifier of this declaration.
        name: Name of this declaration.
        section: Section this declaration belongs to.
        members: Identifiers of member declarations.
        properties: Additional properties.
    """

    def __init__(
        self,
        id: DeclarationId,
        name: str,
        section: Section,
        members: Optional[Sequence[DeclarationId]] = None,
        properties: Properties = None
    ):
        super().__init__(id, name, section, properties=properties)
        self.members = members if members is not None else []


class TypeDeclaration(Declaration):
    """Declaration representing a data type.

    Attributes:
        id: Unique identifier of this declaration.
        name: Name of this declaration.
        section: Section this declaration belongs to.
        datatype: Datatype of the type declaration.
        properties: Additional properties.
    """

    def __init__(
        self,
        id: DeclarationId,
        name: str,
        section: Section,
        datatype: Datatype,
        properties: Optional[Properties] = None
    ):
        super().__init__(id, name, section, properties=properties)
        self.datatype = datatype


class ArrayTypeDeclaration(TypeDeclaration):
    """Declaration representing an array type.

    Attributes:
        id: Unique identifier of this declaration.
        name: Name of this declaration.
        section: Section this declaration belongs to.
        datatype: Datatype of the type declaration.
        arraytype: Datatype of the array elements.
        properties: Additional properties.
    """

    def __init__(
        self,
        id: DeclarationId,
        name: str,
        section: Section,
        datatype: Datatype,
        arraytype: Datatype,
        properties: Optional[Properties] = None
    ):
        super().__init__(id, name, section, datatype, properties=properties)
        self.arraytype = arraytype


class StructTypeDeclaration(TypeDeclaration):
    """Declaration representing a struct type.

    Attributes:
        id: Unique identifier of this declaration.
        name: Name of this declaration.
        section: Section this declaration belongs to.
        datatype: Datatype of the type declaration.
        members: List of member declaration identifiers.
        properties: Additional properties.
    """

    def __init__(
        self,
        id: DeclarationId,
        name: str,
        section: Section,
        datatype: Datatype,
        members: Optional[Sequence[DeclarationId]] = None,
        properties: Optional[Properties] = None
    ):
        super().__init__(id, name, section, datatype, properties=properties)
        self.members = members if members is not None else []


class DataDeclaration(Declaration):
    """Declaration representing actual data associated with a type.

    Attributes:
        id: Unique identifier of this declaration.
        name: Name of this declaration.
        section: Section this declaration belongs to.
        type_id: Identifier of the associated type declaration.
        data: Data payload.
        properties: Additional properties.
    """

    def __init__(
        self,
        id: DeclarationId,
        name: str,
        section: Section,
        type_id: DeclarationId,
        data: Optional[Data] = None,
        properties: Optional[Properties] = None
    ):
        super().__init__(id, name, section, properties=properties)
        self.type_id = type_id
        self.data = data
