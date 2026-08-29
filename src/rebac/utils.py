# rebac/utils.py
import importlib
from functools import lru_cache
from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models

from .backends.base import BaseReBACBackend
from .conf import get_setting


@lru_cache(maxsize=1)
def get_rebac_client() -> BaseReBACBackend:
    """Dynamically loads and instantiates the configured ReBAC backend.

    Returns:
        BaseReBACBackend: An instantiated adapter ready to process authorization queries.

    Raises:
        ImproperlyConfigured: If the backend path is invalid or the class fails to load.
    """
    backend_path: str = get_setting("BACKEND")
    options: dict[str, Any] = get_setting("BACKEND_OPTIONS")

    try:
        module_path, class_name = backend_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        backend_class = getattr(module, class_name)
    except (ImportError, ValueError) as e:
        raise ImproperlyConfigured(
            f"Could not import ReBAC backend '{backend_path}'. Error: {e}"
        ) from e

    if not issubclass(backend_class, BaseReBACBackend):
        raise ImproperlyConfigured(f"Backend '{backend_path}' must inherit from BaseReBACBackend.")

    try:
        return backend_class(**options)
    except Exception as e:
        raise ImproperlyConfigured(
            f"Failed to initialize ReBAC backend '{backend_path}': {e}"
        ) from e


def resolve_rebac_model(label: str) -> type[models.Model]:
    """Resolves an 'app.Model' (or bare 'Model') label to a Django model class.

    T2.7: shared by the `rebac_reconcile` and `rebac_backfill` management commands so
    both label forms work regardless of how many apps the project installs.

    Args:
        label: 'myapp.Folder' or 'Folder'.

    Returns:
        type[models.Model]: The resolved model class.

    Raises:
        LookupError: If no installed app defines a model with that name.
    """
    if "." in label:
        return apps.get_model(label)

    # Bare label: resolve across all installed apps (get_model(None, ...) is not
    # supported in every Django version, so scan the registry directly).
    name = label.lower()
    for app_config in apps.get_app_configs():
        try:
            return app_config.get_model(name)
        except LookupError:
            continue
    raise LookupError(f"Model '{label}' not found in any installed app.")
