# rebac/checks.py
"""Django system checks for django-rebac (T3.12).

Run with ``python manage.py check`` (or any management command — they run
automatically). Every misconfiguration this framework can have is statically
detectable at startup; instead of surfacing as request-time 500s (or, before
T1.1, a silent world-readable collection), they now surface here.

Codes:
    rebac.E001  ERROR    OpenFGA backend selected but STORE_ID is missing
    rebac.E002  ERROR    A RebacViewConfig declares object_type but no relations
                         at all (the T1.1 root cause — a config that protects nothing)
    rebac.E003  ERROR    A RebacModelConfig references a field that does not exist
                         on its model (the T5.17 bug class, caught before request 1)
    rebac.E004  ERROR    LOCAL_DEV_FALLBACK.USE_DJANGO_USER is enabled while
                         DEBUG is off — identity from Django's user in production
    rebac.W001  WARNING  No periodic sweeper (beat schedule) for the outbox drain,
                         so a broker hiccup leaves rows PENDING forever (T2.9)
    rebac.W002  WARNING  Database backend lacks select_for_update(skip_locked),
                         so every sync batch will crash at claim time (T6.18)
"""

import logging
from typing import Any

from django.conf import settings
from django.core import checks
from django.db import DEFAULT_DB_ALIAS

from .conf import get_setting
from .core.structs import iter_registered_view_configs, register_view_config

logger = logging.getLogger(__name__)

# T3.12: idempotency flag for the registration in apps.py (the internal
# checks-registry shape changed across Django versions, so we track our own
# registration state).
_REGISTERED = False

# The Celery task that drains the outbox — beat must schedule something that runs it.
_OUTBOX_DRAIN_TASK = "process_rebac_outbox_batch"


def _scan_url_patterns_for_configs() -> None:
    """Best-effort: import URL modules so their RebacViewConfigs self-register.

    Configs are registered when the class body runs, but view modules often only
    get imported when the URLconf loads. Importing the resolver here (guarded —
    a half-built URLconf must not break ``manage.py check``) catches the common
    case; the registry still covers whatever is already imported.
    """
    try:
        from django.urls import get_resolver

        def _walk(patterns: Any) -> None:
            for pattern in patterns:
                _walk(pattern.url_patterns)
                callback = getattr(pattern, "callback", None)
                view = getattr(callback, "cls", None) or callback
                if isinstance(view, type):
                    config = getattr(view, "rebac_config", None)
                    if config is not None:
                        register_view_config(config)

        _walk(get_resolver().url_patterns)
    except Exception:  # pragma: no cover - URLconf import failure is project-specific
        logger.debug("rebac.E002: URLconf not importable at check time", exc_info=True)


def check_openfga_store_id() -> list[checks.CheckMessage]:
    """rebac.E001: the OpenFGA backend is unusable without a STORE_ID."""
    errors: list[checks.CheckMessage] = []
    backend_path = get_setting("BACKEND")
    if "openfga" not in backend_path:
        return errors

    options = get_setting("BACKEND_OPTIONS")
    if not options.get("STORE_ID"):
        errors.append(
            checks.Error(
                "REBAC_CONFIG['BACKEND_OPTIONS'] is missing 'STORE_ID' — the "
                "OpenFGA backend cannot be initialized, and every authorization "
                "call will fail at request time. Set your store ID "
                "(e.g., os.environ['FGA_STORE_ID']).",
                id="rebac.E001",
            )
        )
    return errors


def check_view_configs_have_relations() -> list[checks.CheckMessage]:
    """rebac.E002: a RebacViewConfig that protects nothing is a misconfiguration.

    Since T1.1 every unresolved relation DENIES, so an all-None config is safe
    (nothing leaks) but useless (nothing works) — exactly the T1.1 failure
    mode, now caught at startup instead of on the first request.
    """
    errors: list[checks.CheckMessage] = []
    _scan_url_patterns_for_configs()

    for config in iter_registered_view_configs():
        if config.object_type and not any(
            (
                config.read_relation,
                config.update_relation,
                config.delete_relation,
                config.list_relation,
                config.action_relations,
            )
        ):
            errors.append(
                checks.Error(
                    f"RebacViewConfig(object_type='{config.object_type}') declares no "
                    "relations at all (read/update/delete/list/action_relations are all "
                    "empty). Since T1.1 an unconfigured relation DENIES, so this view "
                    "protects nothing and allows nothing — you almost certainly meant "
                    "to configure a relation. Set at least one relation, or remove the "
                    "ReBAC config from this view if it is intentionally unprotected.",
                    id="rebac.E002",
                )
            )
    return errors


def check_model_configs_reference_real_fields() -> list[checks.CheckMessage]:
    """rebac.E003: a RebacModelConfig pointing at a non-existent field.

    This is the bug class of the stateless-views doc example (T5.17): the row
    would queue a WRITE with a missing subject, fail in the background worker,
    and surface only as "my list is empty" days later. Catch it at startup.
    """
    errors: list[checks.CheckMessage] = []
    from django.apps import apps

    from .models.mixins import RebacModelSyncMixin

    for model in apps.get_models():
        if not issubclass(model, RebacModelSyncMixin):
            continue
        config = getattr(model, "rebac_config", None)
        if config is None:
            continue

        # Concrete field names AND their attnames: configs may reference an FK's
        # raw column (e.g., `parent_id` for `parent = ForeignKey(...)`), which is
        # valid Django attribute access but not a separate get_fields() entry.
        field_names = set()
        for f in model._meta.get_fields():
            field_names.add(f.name)
            attname = getattr(f, "attname", None)
            if attname:
                field_names.add(attname)
        bad_fields: list[str] = []
        for entry in tuple(config.creators) + tuple(config.parents):
            if entry.local_field not in field_names:
                bad_fields.append(
                    f"local_field '{entry.local_field}' (relation '{entry.relation}')"
                )

        if bad_fields:
            errors.append(
                checks.Error(
                    f"Model '{model.__qualname__}' has a rebac_config referencing "
                    f"field(s) that do not exist on the model: {', '.join(bad_fields)}. "
                    f"Existing fields: {', '.join(sorted(field_names))}. Saving this "
                    "model will crash tuple generation in the background and the "
                    "rows will end up FAILED in the outbox.",
                    id="rebac.E003",
                )
            )
    return errors


def check_local_dev_fallback_not_in_production() -> list[checks.CheckMessage]:
    """rebac.E004: USE_DJANGO_USER implies "local dev", DEBUG=False implies prod."""
    errors: list[checks.CheckMessage] = []
    fallback = get_setting("LOCAL_DEV_FALLBACK") or {}
    if fallback.get("USE_DJANGO_USER") and not settings.DEBUG:
        errors.append(
            checks.Error(
                "REBAC_CONFIG['LOCAL_DEV_FALLBACK']['USE_DJANGO_USER'] is True while "
                "DEBUG is False. USE_DJANGO_USER is a LOCAL-DEVELOPMENT fallback: it "
                "takes the ReBAC subject from Django's authenticated user. In "
                "production, identity must come from your trusting proxy (Traefik "
                "forward-auth) via REQUEST_HEADER_MAPPINGS, or from TRUSTED_PROXIES. "
                "Set USE_DJANGO_USER=False.",
                id="rebac.E004",
            )
        )
    return errors


def check_beat_sweeper_scheduled() -> list[checks.CheckMessage]:
    """rebac.W001: the periodic sweeper is load-bearing, not a fail-safe (T2.9)."""
    warnings: list[checks.CheckMessage] = []

    # INLINE mode drains in-process on every write — no sweeper needed.
    if get_setting("SYNC_MODE") == "INLINE":
        return warnings

    try:
        import celery  # noqa: F401
    except ImportError:
        return warnings

    beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", None)
    if not beat_schedule:
        return [
            checks.Warning(
                "REBAC outbox: no CELERY_BEAT_SCHEDULE is defined. The "
                f"'{_OUTBOX_DRAIN_TASK}' drain is only dispatched on write — if a "
                "broker hiccup swallows that dispatch, rows sit PENDING forever "
                "with nothing to pick them up. Schedule a periodic entry that runs "
                f"'{_OUTBOX_DRAIN_TASK}' (recommended: every 60s).",
                id="rebac.W001",
            )
        ]

    if not any(_OUTBOX_DRAIN_TASK in str(entry) for entry in beat_schedule.values()):
        warnings.append(
            checks.Warning(
                f"CELERY_BEAT_SCHEDULE is defined but no entry runs the ReBAC outbox "
                f"drain task '{_OUTBOX_DRAIN_TASK}'. A broker hiccup during the "
                "post-commit dispatch would leave rows PENDING forever — add a "
                "periodic entry for the drain (recommended: every 60s).",
                id="rebac.W001",
            )
        )
    return warnings


def check_database_supports_skip_locked() -> list[checks.CheckMessage]:
    """rebac.W002: the claim relies on select_for_update(skip_locked=True) (T6.18)."""
    warnings: list[checks.CheckMessage] = []
    try:
        from django.db import connections

        if DEFAULT_DB_ALIAS not in settings.DATABASES:
            return warnings

        features = connections[DEFAULT_DB_ALIAS].features
        if not features.has_select_for_update_skip_locked:
            warnings.append(
                checks.Warning(
                    "The default database backend does not support "
                    "select_for_update(skip_locked=True) (needs Postgres, MySQL "
                    ">= 8.0.1, or MariaDB >= 10.6). Every ReBAC outbox drain will "
                    "crash at claim time with NotSupportedError. Move the outbox to "
                    "a supported backend.",
                    id="rebac.W002",
                )
            )
    except Exception:  # pragma: no cover - features access is static, but be safe
        logger.debug("rebac.W002: could not inspect DB features", exc_info=True)
    return warnings


def run_rebac_system_checks(app_configs: list[Any], **kwargs: Any) -> list[checks.CheckMessage]:
    """T3.12: the django-rebac check suite.

    Runs every check above and returns their messages; ``manage.py check``
    (and every management command) reports them at startup instead of the
    failures surfacing per-request.

    Registered from ``ReBACConfig.ready()`` in apps.py.
    """
    check_fns = [
        check_openfga_store_id,
        check_view_configs_have_relations,
        check_model_configs_reference_real_fields,
        check_local_dev_fallback_not_in_production,
        check_beat_sweeper_scheduled,
        check_database_supports_skip_locked,
    ]

    messages: list[checks.CheckMessage] = []
    for check in check_fns:
        messages.extend(check())
    return messages
