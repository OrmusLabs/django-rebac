# rebac/conf.py
import importlib
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from rebac.loggers import RebacConsoleLogger

dev_logger = RebacConsoleLogger(__name__)

# Sensible defaults so developers don't have to define everything
DEFAULTS: dict[str, Any] = {
    "BACKEND": "rebac.backends.openfga.client.OpenFGABackend",
    "BACKEND_OPTIONS": {},
    "BATCH_SIZE": 50,
    "MAX_RETRIES": 5,
    "REQUEST_HEADER_MAPPINGS": {
        "X-User-Id": "rebac_user",
        # "X-Context-Org-Id": "rebac_tenant",
        # "X-Department-Id": "rebac_department",
        # "X-Clearance-Level": "rebac_clearance",
    },
    # Enables the Django Admin panel for monitoring the ReBAC Outbox
    "ENABLE_OUTBOX_ADMIN": True,
    # Tells the Mixins/Permissions which attribute to use for ReBAC checks
    "REBAC_USER_ATTR": "rebac_user",
    # Prefix added automatically to the user ID
    "REBAC_USER_PREFIX": "user:",
    # Local Dev Settings - Remove it for Production!
    "LOCAL_DEV_FALLBACK": {
        # If True, falls back to Django's native session/token user if the Gateway is missing
        "USE_DJANGO_USER": True,
        # Optional: A hardcoded string fallback if you don't want to use the database at all
        "STATIC_USER_ID": None,
    },
}
"""Sensible defaults for the django-rebac integration.

Attributes:
    BACKEND (str): The dot-path to the ReBAC engine adapter.
        Defaults to `django_rebac.backends.openfga.client.OpenFGABackend`.
    BACKEND_OPTIONS (dict[str, Any]): Backend-specific configuration 
        (e.g., API URLs, Store IDs, or Pre-shared Keys).
    BATCH_SIZE (int): Number of items to process in a single synchronization batch.
         Defaults to `50`.
    MAX_RETRIES (int): How many times to retry failed synchronization attempts.
         Defaults to `5`.
    REQUEST_HEADER_MAPPINGS (dict[str, str]): Mapping of incoming request
        headers to ReBAC context variables.
    ENABLE_OUTBOX_ADMIN (bool): If True, registers the ReBAC Outbox model in
        the Django Admin. Defaults to `True`.
    REBAC_USER_ATTR (str): The attribute on the request/user object to use
        for ReBAC identity.
    REBAC_USER_PREFIX (str): Prefix added to user IDs (e.g., `user:123`).
    LOCAL_DEV_FALLBACK (dict[str, Any]): Settings for local development
        when identity providers are absent.

        - **USE_DJANGO_USER**: Fallback to native Django session user.
             Defaults to `True`.
        - **STATIC_USER_ID**: A hardcoded ID for rapid testing.
             Defaults to `None`.
"""


def _resolve_backend_options(user_settings: dict[str, Any]) -> dict[str, Any]:
    """Dynamically loads the backend's defaults and merges them with user settings.

    Args:
        user_settings: The raw dictionary parsed from django.conf.settings.

    Returns:
        dict[str, Any]: A merged dictionary prioritizing user options over defaults.
    """
    active_backend_path: str = user_settings.get("BACKEND", DEFAULTS["BACKEND"])
    user_options: dict[str, Any] = user_settings.get("BACKEND_OPTIONS", {})

    try:
        # e.g., 'rebac.backends.openfga.client.OpenFGABackend'
        # -> 'rebac.backends.openfga'
        backend_module_path = active_backend_path.rsplit(".", 2)[0]
        defaults_module_path = f"{backend_module_path}.defaults"

        # Import the defaults.py file from the specific backend subpackage
        defaults_module = importlib.import_module(defaults_module_path)
        specific_defaults: dict[str, Any] = getattr(defaults_module, "BACKEND_DEFAULTS", {}).copy()
    except (ImportError, AttributeError):
        # If the user builds a custom backend without a defaults.py, fail gracefully
        specific_defaults = {}

    # User options strictly overwrite the backend's defaults
    specific_defaults.update(user_options)

    return specific_defaults


def get_setting(name: str) -> Any:
    """Fetches a setting from the `REBAC` dictionary in `django.conf.settings`.

    Falls back to the `DEFAULTS` dictionary if not provided.

    Args:
        name: The string key of the setting to retrieve.

    Returns:
        Any: The resolved configuration value.

    Raises:
        ImproperlyConfigured: If the setting key is not recognized by the framework.
    """
    if name not in DEFAULTS:
        raise ImproperlyConfigured(f"'{name}' is not a valid django-rebac setting.")

    # Using 'REBAC_CONFIG' as the standard namespace to match standard Django conventions
    user_settings: dict[str, Any] = getattr(settings, "REBAC_CONFIG", {})

    if name == "BACKEND_OPTIONS":
        return _resolve_backend_options(user_settings)

    return user_settings.get(name, DEFAULTS[name])


def validate_settings() -> None:
    """Ensures the ReBAC configuration is logically consistent.

    Should be called in AppConfig.ready().

    Raises:
        ImproperlyConfigured: If the configuration prevents the framework from operating.
    """
    user_attr = get_setting("REBAC_USER_ATTR")
    mappings = get_setting("REQUEST_HEADER_MAPPINGS")

    # THE HANDSHAKE CHECK
    # Ensure the attribute we use for checks is actually being populated by the middleware
    if user_attr not in mappings.values():
        raise ImproperlyConfigured(
            f"REBAC['REBAC_USER_ATTR'] is set to '{user_attr}', "
            f"but this attribute is not defined as a target in 'REQUEST_HEADER_MAPPINGS'. "
            f"The GatewayIdentityMiddleware will never be able to set the user context."
        )

    # Ensure the prefix is provided for Zanzibar compatibility
    prefix = get_setting("REBAC_USER_PREFIX")
    if not prefix or not prefix.endswith(":"):
        # Warning: They forgot the colon in 'user:'
        dev_logger.warning(
            f"REBAC_USER_PREFIX ('{prefix}') does not end with a colon. "
            f"Standard ReBAC object strings usually follow 'type:id' format."
        )
