from __future__ import annotations
from psf_parser.declaration import Section, Declaration, DeclarationId, GroupDeclaration


class Registry:
    """Registry to manage declarations and lookup by id or name.

    Declarations can be looked up by identifier or by (section, scope, name).

    Attributes:
        declarations: Mapping from declaration ids to declarations.
        types: List of declarations belonging to the TYPE section.
        sweeps: List of declarations belonging to the SWEEP section.
        traces: List of declarations belonging to the TRACE section.
        values: List of declarations belonging to the VALUE section.
    """

    def __init__(self):
        """Initialize an empty registry."""
        self.declarations: dict[DeclarationId, Declaration] = dict()
        self._name_map: dict[tuple[Section, tuple[str, ...], str], Declaration] = dict()
        self._next_id: int = 1

    def generate_id(self) -> int:
        """Generate a unique identifier for a new declaration.

        Returns:
            int: A unique declaration id.
        """
        decl_id = self._next_id
        self._next_id += 1
        return decl_id

    def add(self, decl: Declaration, scope: tuple[str, ...] = ()) -> None:
        """Add a declaration to the registry.

        Args:
            decl: The declaration to add.
            scope: Scope tuple representing nested group names.

        Raises:
            ValueError: If a duplicate id or scoped name is detected.
        """
        name_map_key = (decl.section, scope, decl.name)

        if decl.id in self.declarations:
            raise ValueError(f'Error: Duplicate id: {decl.id}')
        if name_map_key in self._name_map:
            scoped_name = "::".join((decl.section.name,) + scope + (decl.name,))
            raise ValueError(f'Error: Duplicate scoped name: {scoped_name}')

        self.declarations[decl.id] = decl
        self._name_map[name_map_key] = decl

    def get_by_id(self, decl_id: DeclarationId) -> Declaration | None:
        """Get a declaration by its id.

        Args:
            decl_id: The id of the declaration.

        Returns:
            Declaration | None: The corresponding declaration, if found.
        """
        return self.declarations.get(decl_id)

    def get_by_name(self, name: str, section: Section, scope: tuple[str, ...] = ()) -> Declaration | None:
        """Get a declaration by its name, section and scope.

        Args:
            name: Name of the declaration.
            section: Section of the declaration.
            scope: Scope representing nested group names.

        Returns:
            Declaration | None: The matching declaration, if found.
        """
        return self._name_map.get((section, scope, name))

    def get_by_flat_name(self, name: str) -> list[Declaration]:
        """Get all declarations that match a given name across all scopes and sections.

        Args:
            name (str): The flat (unscoped) name to search for.

        Returns:
            list[Declaration]: List of matching declarations.
        """
        return [decl for scoped, decl in self._name_map.items() if scoped[-1] == name]

    @property
    def types(self) -> list[Declaration]:
        """List all declarations belonging to the TYPE section."""
        return [decl for decl in self.declarations.values() if decl.section is Section.TYPE]

    @property
    def sweeps(self) -> list[Declaration]:
        """List all declarations belonging to the SWEEP section, excluding GroupDeclarations."""
        return [
            decl for decl in self.declarations.values()
            if decl.section is Section.SWEEP and not isinstance(decl, GroupDeclaration)
        ]

    @property
    def traces(self) -> list[Declaration]:
        """List all declarations belonging to the TRACE section, excluding GroupDeclarations."""
        return [
            decl for decl in self.declarations.values()
            if decl.section is Section.TRACE and not isinstance(decl, GroupDeclaration)
        ]

    @property
    def values(self) -> list[Declaration]:
        """List all declarations belonging to the VALUE section."""
        return [decl for decl in self.declarations.values() if decl.section is Section.VALUE]

    def __repr__(self) -> str:
        return (
            f'<Registry: {len(self.types)} types, {len(self.sweeps)} sweeps, '
            f'{len(self.traces)} traces, {len(self.values)} values>'
        )

