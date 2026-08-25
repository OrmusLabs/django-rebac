"""Flat re-export of the ReBAC tuple adapter.

The canonical definition lives in :mod:`rebac.core.adapters`. This module exists so that
the documented import path works::

    from rebac.adapters import RebacTupleAdapter

The adapter is a stateless helper over the configuration dataclasses and does not import
any Django model, so it is safe to import eagerly.
"""

from .core.adapters import RebacTupleAdapter

__all__ = ["RebacTupleAdapter"]
