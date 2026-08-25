"""Flat re-exports of the ReBAC mixins.

The canonical definitions live in :mod:`rebac.models.mixins` and :mod:`rebac.views.mixins`.
This module exists so that the documented import paths work::

    from rebac.mixins import RebacModelSyncMixin, RebacViewMixin

``RebacModelSyncMixin`` pulls in the :class:`~rebac.models.outbox.RebacSyncOutbox` model, so
this module must not be imported before the Django app registry is populated. That is not a
practical restriction: it is designed to be imported from a consuming app's ``models.py`` or
``views.py``, both of which Django loads after the registry is ready. The package root
(:mod:`rebac`) resolves the same names lazily for the same reason.
"""

from .models.mixins import RebacModelSyncMixin
from .views.mixins import RebacViewMixin

__all__ = [
    "RebacModelSyncMixin",
    "RebacViewMixin",
]
