# rebac/structs.py
from dataclasses import dataclass, field

__all__ = [
    "RebacCreatorConfig",
    "RebacModelConfig",
    "RebacParentConfig",
    "RebacViewConfig",
]


@dataclass(frozen=True)
class RebacParentConfig:
    """Defines how a child object relates to its parent in the ReBAC authorization graph.

    This configuration establishes a "Structural Link" between objects, enabling
    inherited access patterns. For example, a "folder" belongs to an "organization",
    allowing subjects with access to the organization to automatically inherit access
    to the folder based on your ReBAC schema.

    Attributes:
        relation: The structural relationship name in the ReBAC model (e.g., "organization",
            "parent"). This must match a relation defined in your schema.
        parent_type: The ReBAC object type of the parent entity (e.g., "organization",
            "workspace"). The format should match your type definitions.
        local_field: The Django model field name that stores the parent's primary key.
            This field should be a ForeignKey or contain the parent object's ID.

    Example:
        ```python
        RebacParentConfig(
            relation="organization",
            parent_type="organization",
            local_field="org_id"
        )
        ```
    """

    relation: str
    parent_type: str
    local_field: str


@dataclass(frozen=True)
class RebacCreatorConfig:
    """Defines the ownership role assigned to a subject when they create an object.

    This configuration automatically grants the creator specific roles on the
    object they create, implementing the "creator owns their content" pattern. The
    relationship is established at creation time via Django's save lifecycle.

    Attributes:
        relation: The Role name in the ReBAC model representing ownership
            (e.g., "owner", "editor", "author"). This must match a role defined
            in your schema that accepts the subject type.
        local_field: The Django model field name that stores the creator's subject ID.
            Typically, this is a ForeignKey to the User model or a UUID field
            named "creator_id", "owner_id", etc.
        user_type: The ReBAC type for the creator (defaults to "user").
            Override this if the creator is a machine role, API key, or team.

    Example:
        ```python
        RebacCreatorConfig(
            relation="editor",
            local_field="creator_id"
        )
        ```
    """

    relation: str
    local_field: str
    user_type: str = "user"


@dataclass(frozen=True)
class RebacModelConfig:
    """Complete configuration for synchronizing a Django model with ReBAC relationship tuples.

    This is the primary configuration class that defines how a Django model maps to ReBAC
    objects, relationships, and ownership patterns. The configuration drives automatic
    tuple creation/deletion when model instances are created, updated, or deleted.

    Attributes:
        object_type: The ReBAC type name for this Django model (e.g., "document",
            "folder"). This should match your schema's type definitions exactly.
        parents: List of parent relationship configurations that establish structural
            access patterns. Each RebacParentConfig defines how this object relates to
            parent objects, enabling inherited permissions.
        creators: List of creator relationship configurations that automatically grant
            ownership roles to subjects who create instances. Each RebacCreatorConfig
            defines which field represents the creator and what role to assign.

    Example:
        ```python
        RebacModelConfig(
            object_type="document",
            parents=[
                RebacParentConfig(
                    relation="folder",
                    parent_type="folder",
                    local_field="folder_id"
                )
            ],
            creators=[
                RebacCreatorConfig(
                    relation="editor",
                    local_field="creator_id"
                )
            ]
        )
        ```

    Raises:
        ValueError: If `object_type` is empty, or if duplicate relations are defined
            across parents and creators (which would cause tuple conflicts).
    """

    object_type: str
    parents: list[RebacParentConfig] = field(default_factory=list)
    creators: list[RebacCreatorConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validates configuration state immediately upon instantiation."""
        if not self.object_type:
            raise ValueError("RebacModelConfig must define an 'object_type'.")

        # Collect all relations to check for duplicates
        parent_relations = {p.relation for p in self.parents}
        creator_relations = {c.relation for c in self.creators}

        # Check for overlapping relations between parents and creators
        overlaps = parent_relations & creator_relations
        if overlaps:
            raise ValueError(
                f"RebacModelConfig cannot define the same relation(s) in both "
                f"'parents' and 'creators': {overlaps}. "
                f"This would cause ambiguous tuple generation. "
                f"Models should not assign permissions directly; use either parent "
                f"inheritance OR creator assignment, not both for the same relation."
            )


@dataclass(frozen=True)
class RebacViewConfig:
    """Configuration for enforcing ReBAC authorization checks on Django views and ViewSets.

    This dataclass centralizes all authorization settings needed to protect API endpoints
    with ReBAC checks. It supports multiple authorization strategies including object-level
    permission checks, filtered list queries, and custom action-based relations.

    Attributes:
        object_type: The ReBAC type name for objects managed by this view (e.g., "document",
            "workspace"). Must match the type used in your schema and model configurations.
        list_relation: The relation required specifically to list objects (GET /api/docs/).
            If omitted, the framework safely falls back to using `read_relation`.
        disable_list_filter: A strict boolean flag to explicitly bypass ReBAC
            filtering on list endpoints. Defaults to False.
        read_relation: The relation required to view/list objects (e.g., "can_read_document").
            Set to None to skip read authorization checks.
        update_relation: The relation required to modify existing objects (e.g., "can_update").
            Checked on PUT/PATCH requests. Set to None to skip checks.
        delete_relation: The relation required to remove objects (e.g., "can_delete").
            Checked on DELETE requests. Set to None to skip checks.
        lookup_header: An optional HTTP header name (e.g., "HTTP_X_CONTEXT_ORG_ID")
            used to extract the target object ID statelessly, bypassing database lookups.
        lookup_url_kwarg: An optional URL kwarg name (e.g., "org_id")
            used to extract the target object ID from the router statelessly.
        create_scope_type: For POST/creation requests, the type of the parent
            object that must exist (e.g., "folder"). Requires `create_scope_field` and
            `create_relation` to also be set.
        create_scope_field: The Django model field containing the parent object's ID.
            Extracted from the request data to build the parent object reference.
        create_relation: The permission required on the parent object to allow
            creation (e.g., "can_add_items"). Checked before allowing object creation.
        action_relations: Mapping of custom ViewSet action names to ReBAC permissions.
            Keys must match the action names in your ViewSet's `@action` decorators.

    Raises:
        ValueError: If `create_scope_type`, `create_scope_field`, and `create_relation` are
            partially defined (all or none must be provided).
    """

    object_type: str

    list_relation: str | None = None
    disable_list_filter: bool = False

    read_relation: str | None = None
    update_relation: str | None = None
    delete_relation: str | None = None

    # Stateless Resolution
    lookup_header: str | None = None
    lookup_url_kwarg: str | None = None

    # Parent verification for POST/Creation
    create_scope_type: str | None = None
    create_scope_field: str | None = None
    create_relation: str | None = None

    # Custom ViewSet actions mapping
    action_relations: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Fail fast if parent creation settings are partially defined."""
        parent_configs = [
            self.create_scope_type,
            self.create_scope_field,
            self.create_relation,
        ]

        if any(parent_configs) and not all(parent_configs):
            raise ValueError(
                "If defining ReBAC parent creation rules, 'create_scope_type', "
                "'create_scop_field', and 'create_relation' must all be provided."
            )
