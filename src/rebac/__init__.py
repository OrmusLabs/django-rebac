"""django-rebac: a declarative, strictly-typed ReBAC framework for Django.

This package root exposes the flat public API::

    from rebac import RebacModelSyncMixin, RebacViewConfig, IsRebacAuthorized

Every name is resolved lazily on first access. This is required, not stylistic: ``rebac`` is
listed in ``INSTALLED_APPS``, so Django imports this module while it is still building the app
registry. Importing :class:`~rebac.models.outbox.RebacSyncOutbox` (or anything that transitively
imports it) at module scope would raise ``AppRegistryNotReady`` on every boot. Deferring the
import to attribute access moves it past registry population.

The same names remain available from their canonical modules (:mod:`rebac.models`,
:mod:`rebac.views`, :mod:`rebac.core.structs`, ...) and from the flat re-export modules
:mod:`rebac.mixins`, :mod:`rebac.structs`, and :mod:`rebac.adapters`.
"""

from typing import TYPE_CHECKING, Any

from ._lazy import ROOT_ATTRIBUTES, resolve_lazy_attr

__all__ = [
    "IsRebacAuthorized",
    "RebacCreatorConfig",
    "RebacModelConfig",
    "RebacModelSyncMixin",
    "RebacParentConfig",
    "RebacPermissionSerializerMixin",
    "RebacSyncOutbox",
    "RebacTupleAdapter",
    "RebacViewConfig",
    "RebacViewMixin",
]


def __getattr__(name: str) -> Any:
    """Resolves a public name on first access. See the module docstring for why."""
    return resolve_lazy_attr(__name__, name, ROOT_ATTRIBUTES)


def __dir__() -> list[str]:
    return sorted(__all__)


# Give type checkers and IDEs the real symbols. This block never executes at runtime, so it
# cannot trigger the AppRegistryNotReady problem described above.
if TYPE_CHECKING:
    from .core.adapters import RebacTupleAdapter
    from .core.structs import (
        RebacCreatorConfig,
        RebacModelConfig,
        RebacParentConfig,
        RebacViewConfig,
    )
    from .models.mixins import RebacModelSyncMixin
    from .models.outbox import RebacSyncOutbox
    from .permissions import IsRebacAuthorized
    from .serializers.mixins import RebacPermissionSerializerMixin
    from .views.mixins import RebacViewMixin
