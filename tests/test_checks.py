# tests/test_checks.py
"""T3.12: Django system checks for django-rebac."""

import pytest
from django.core import checks

from rebac import checks as rebac_checks
from rebac.core.structs import (
    RebacViewConfig,
    clear_registered_view_configs,
)


@pytest.fixture(autouse=True)
def _clean_view_config_registry():
    """The view-config registry is process-global; keep it isolated per test."""
    clear_registered_view_configs()
    yield
    clear_registered_view_configs()


class TestRebacE001StoreId:
    def test_missing_store_id_reports_error(self, settings):
        settings.REBAC_CONFIG = {"BACKEND_OPTIONS": {}}

        messages = rebac_checks.check_openfga_store_id()

        assert [m.id for m in messages] == ["rebac.E001"]
        assert messages[0].level == checks.ERROR

    def test_store_id_configured_passes(self, settings):
        settings.REBAC_CONFIG = {"BACKEND_OPTIONS": {"STORE_ID": "01H0H0H0H0H0H0H0H0H0H0H0H0"}}

        assert rebac_checks.check_openfga_store_id() == []

    def test_non_openfga_backend_skips_store_check(self, settings):
        settings.REBAC_CONFIG = {
            "BACKEND": "myapp.backends.spicedb.SpineDBBackend",
            "BACKEND_OPTIONS": {},
        }

        assert rebac_checks.check_openfga_store_id() == []


class TestRebacE002ViewConfigs:
    def test_view_config_without_relations_reports_error(self):
        """T1.1's root cause: a config that protects nothing is caught at startup."""
        RebacViewConfig(object_type="invoice")

        messages = rebac_checks.check_view_configs_have_relations()

        assert [m.id for m in messages] == ["rebac.E002"]
        assert "invoice" in str(messages[0])

    def test_view_config_with_relation_passes(self):
        RebacViewConfig(object_type="document", read_relation="viewer")

        assert rebac_checks.check_view_configs_have_relations() == []

    def test_view_config_with_only_action_relations_passes(self):
        RebacViewConfig(object_type="folder", action_relations={"share": "can_share"})

        assert rebac_checks.check_view_configs_have_relations() == []

    def test_no_registered_view_configs_passes(self):
        assert rebac_checks.check_view_configs_have_relations() == []


class TestRebacE003ModelConfigs:
    def test_model_configs_with_valid_fields_pass(self):
        """The suite's own mock models (creator_id / org_id / parent_id) are valid.

        NOTE: defined BEFORE the broken-model test — that one registers a
        misconfigured model in the app registry, which this test must not see.
        """
        assert rebac_checks.check_model_configs_reference_real_fields() == []

    def test_model_config_referencing_missing_field_reports_error(self):
        from django.db import models

        from rebac.core.structs import RebacCreatorConfig, RebacModelConfig
        from rebac.models.mixins import RebacModelSyncMixin

        class BrokenRebacModel(RebacModelSyncMixin, models.Model):
            name = models.CharField(max_length=10)

            rebac_config = RebacModelConfig(
                object_type="broken",
                creators=[RebacCreatorConfig(relation="owner", local_field="nonexistent_field")],
            )

            class Meta:
                app_label = "rebac"

        messages = rebac_checks.check_model_configs_reference_real_fields()

        assert [m.id for m in messages] == ["rebac.E003"]
        assert "nonexistent_field" in str(messages[0])


class TestRebacE004LocalDevFallback:
    def test_use_django_user_with_debug_off_reports_error(self, settings):
        settings.DEBUG = False
        settings.REBAC_CONFIG = {"LOCAL_DEV_FALLBACK": {"USE_DJANGO_USER": True}}

        messages = rebac_checks.check_local_dev_fallback_not_in_production()

        assert [m.id for m in messages] == ["rebac.E004"]

    def test_use_django_user_with_debug_on_passes(self, settings):
        settings.DEBUG = True
        settings.REBAC_CONFIG = {"LOCAL_DEV_FALLBACK": {"USE_DJANGO_USER": True}}

        assert rebac_checks.check_local_dev_fallback_not_in_production() == []

    def test_use_django_user_off_in_production_passes(self, settings):
        settings.DEBUG = False
        settings.REBAC_CONFIG = {"LOCAL_DEV_FALLBACK": {"USE_DJANGO_USER": False}}

        assert rebac_checks.check_local_dev_fallback_not_in_production() == []


class TestRebacW001BeatSweeper:
    def test_no_beat_schedule_reports_warning(self, settings):
        settings.CELERY_BEAT_SCHEDULE = {}

        messages = rebac_checks.check_beat_sweeper_scheduled()

        assert [m.id for m in messages] == ["rebac.W001"]
        assert messages[0].level == checks.WARNING

    def test_beat_schedule_with_drain_passes(self, settings):
        settings.CELERY_BEAT_SCHEDULE = {
            "rebac-outbox-drain": {
                "task": "rebac.tasks.process_rebac_outbox_batch",
                "schedule": 60.0,
            }
        }

        assert rebac_checks.check_beat_sweeper_scheduled() == []

    def test_inline_sync_mode_skips_beat_check(self, settings):
        """INLINE drains in-process on every write — no sweeper needed."""
        settings.CELERY_BEAT_SCHEDULE = {}
        settings.REBAC_CONFIG = {"SYNC_MODE": "INLINE"}

        assert rebac_checks.check_beat_sweeper_scheduled() == []


class TestRebacW002SkipLocked:
    def test_sqlite_lacks_skip_locked_reports_warning(self):
        """The test DB is SQLite (no FOR UPDATE SKIP LOCKED) → the warning fires,
        which is exactly the signal a real deployment needs before the first drain."""
        messages = rebac_checks.check_database_supports_skip_locked()

        assert [m.id for m in messages] == ["rebac.W002"]
        assert messages[0].level == checks.WARNING


class TestSuiteRegistration:
    def test_check_suite_is_registered_in_django_registry(self):
        """apps.ready() must register the suite so `manage.py check` runs it."""
        try:
            from django.core.checks import registry as _checks_registry  # Django >= 5

            registered = _checks_registry.registry.registered_checks
        except ImportError:
            from django.core import checks as core_checks

            registered = {
                item.function for items in core_checks._registry.values() for item in items
            }

        assert rebac_checks.run_rebac_system_checks in registered

    def test_run_rebac_system_checks_aggregates_messages(self, settings):
        """The entry point runs every check and merges their messages."""
        settings.REBAC_CONFIG = {}  # no STORE_ID → E001; USE_DJANGO_USER off → no E004
        settings.CELERY_BEAT_SCHEDULE = {}  # → W001
        RebacViewConfig(object_type="invoice")  # → E002

        messages = rebac_checks.run_rebac_system_checks(app_configs=[])
        ids = {m.id for m in messages}

        # Superset: other checks may fire too (e.g., E003 if a broken model was
        # defined by an earlier test in this file).
        assert {"rebac.E001", "rebac.E002", "rebac.W001", "rebac.W002"} <= ids
