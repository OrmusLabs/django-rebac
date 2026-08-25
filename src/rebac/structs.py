"""Flat re-exports of the ReBAC configuration dataclasses.

The canonical definitions live in :mod:`rebac.core.structs`. This module exists so that
the documented import path works::

    from rebac.structs import RebacModelConfig, RebacParentConfig

These are plain frozen dataclasses with no Django model dependencies, so they are safe to
import eagerly from anywhere, including a consuming app's ``models.py``.
"""

from .core.structs import (
    RebacCreatorConfig,
    RebacModelConfig,
    RebacParentConfig,
    RebacViewConfig,
)

__all__ = [
    "RebacCreatorConfig",
    "RebacModelConfig",
    "RebacParentConfig",
    "RebacViewConfig",
]
