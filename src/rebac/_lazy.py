"""Internal helper for PEP 562 lazy module attributes.

The ``rebac`` package is listed in ``INSTALLED_APPS``, so Django imports it while it is
still populating the app registry. Importing a Django model at that point raises
``AppRegistryNotReady``, which means the flat public API cannot be built from eager
``from .models import ...`` statements at module scope.

Instead the name -> module mapping lives here and the real import is deferred until the
attribute is first accessed, which always happens after the registry is ready.

The mapping is kept in this module rather than in ``rebac/__init__.py`` so the package root
stays a docstring-and-re-exports module (ruff's RUF067).
"""

from importlib import import_module
from typing import Any

#: Maps each name in ``rebac.__all__`` to the canonical module that defines it.
ROOT_ATTRIBUTES: dict[str, str] = {
    "IsRebacAuthorized": "rebac.permissions",
    "RebacCreatorConfig": "rebac.core.structs",
    "RebacModelConfig": "rebac.core.structs",
    "RebacModelSyncMixin": "rebac.models.mixins",
    "RebacParentConfig": "rebac.core.structs",
    "RebacPermissionSerializerMixin": "rebac.serializers.mixins",
    "RebacSyncOutbox": "rebac.models.outbox",
    "RebacTupleAdapter": "rebac.core.adapters",
    "RebacViewConfig": "rebac.core.structs",
    "RebacViewMixin": "rebac.views.mixins",
}


def resolve_lazy_attr(module_name: str, name: str, targets: dict[str, str]) -> Any:
    """Resolves one lazily-exported attribute, importing its module on demand.

    Args:
        module_name: The ``__name__`` of the calling module, used for error messages.
        name: The attribute being accessed.
        targets: Mapping of attribute name to the dotted module path that defines it.

    Returns:
        Any: The requested attribute.

    Raises:
        AttributeError: If `name` is not an exported attribute, matching the message
            CPython would raise for a genuinely missing one.
    """
    try:
        target = targets[name]
    except KeyError:
        raise AttributeError(f"module {module_name!r} has no attribute {name!r}") from None
    return getattr(import_module(target), name)
