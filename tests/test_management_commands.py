# tests/test_management_commands.py
"""Regression guards for the `rebac_backfill` and `rebac_reconcile` commands."""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from rebac.backends.base.exceptions import RebacConnectionError
from rebac.models import RebacSyncOutbox
from rebac.utils import resolve_rebac_model
from tests.models import MockFolder

pytestmark = pytest.mark.django_db


def _clear_outbox() -> None:
    """Removes rows the sync mixin queued during fixture setup (eager Celery)."""
    RebacSyncOutbox.objects.all().delete()


def _pending() -> list[tuple]:
    return list(
        RebacSyncOutbox.objects.filter(status=RebacSyncOutbox.Status.PENDING)
        .order_by("id")
        .values_list("action", "user_id", "relation", "object_id")
    )


def _folder() -> MockFolder:
    folder = MockFolder.objects.create(name="f1", org_id="org-1", creator_id="alice")
    _clear_outbox()
    return folder


class TestResolveRebacModel:
    def test_resolves_bare_and_qualified_labels(self):
        assert resolve_rebac_model("MockFolder") is MockFolder
        assert resolve_rebac_model("rebac.MockFolder") is MockFolder

    def test_unknown_label_raises(self):
        with pytest.raises(LookupError):
            resolve_rebac_model("DoesNotExist")


class TestRebacBackfill:
    def test_enqueues_writes_for_existing_rows(self):
        folder = _folder()
        out = StringIO()
        call_command("rebac_backfill", "rebac.MockFolder", "--no-sync", stdout=out)

        assert _pending() == [
            (
                RebacSyncOutbox.Action.WRITE,
                "organization:org-1",
                "organization",
                f"folder:{folder.pk}",
            ),
            (
                RebacSyncOutbox.Action.WRITE,
                "user:alice",
                "owner",
                f"folder:{folder.pk}",
            ),
        ]
        assert "1 row(s) scanned, 2 tuple(s) enqueued" in out.getvalue()

    def test_triggers_worker_once_by_default(self):
        _folder()
        with (
            patch(
                "rebac.management.commands.rebac_backfill.RebacTupleIngestionService.queue_tuples",
                return_value=2,
            ) as queue,
            patch(
                "rebac.management.commands.rebac_backfill.RebacTupleIngestionService.trigger_sync"
            ) as trigger,
        ):
            call_command("rebac_backfill", "MockFolder")

        queue.assert_called_once()
        trigger.assert_called_once()

    def test_dry_run_does_not_enqueue(self):
        _folder()
        out = StringIO()
        call_command("rebac_backfill", "MockFolder", "--dry-run", stdout=out)

        assert RebacSyncOutbox.objects.count() == 0
        value = out.getvalue()
        assert "dry-run" in value and "2 tuple(s) would be enqueued" in value

    def test_unknown_model_raises_command_error(self):
        with pytest.raises(CommandError, match="Unknown model"):
            call_command("rebac_backfill", "DoesNotExist")

    def test_rejects_non_rebac_model(self):
        with pytest.raises(CommandError, match="RebacModelSyncMixin"):
            call_command("rebac_backfill", "Invoice")


EXPECTED_TUPLES = [
    {"user": "organization:org-1", "relation": "organization", "object": "folder:1"},
    {"user": "user:alice", "relation": "owner", "object": "folder:1"},
]


class TestRebacReconcile:
    @pytest.fixture
    def folder(self) -> MockFolder:
        return _folder()

    def test_reports_clean_when_store_matches(self, folder):
        with patch("rebac.management.commands.rebac_reconcile.get_rebac_client") as get_client:
            get_client.return_value.read_tuples.return_value = EXPECTED_TUPLES
            out = StringIO()
            call_command("rebac_reconcile", "MockFolder", stdout=out)

        assert "no drift" in out.getvalue()
        assert RebacSyncOutbox.objects.count() == 0

    def test_detects_drift_without_applying(self, folder):
        # Store is missing the owner grant and holds a stale viewer tuple.
        stored = [
            {"user": "organization:org-1", "relation": "organization", "object": "folder:1"},
            {"user": "user:bob", "relation": "viewer", "object": "folder:1"},
        ]
        with patch("rebac.management.commands.rebac_reconcile.get_rebac_client") as get_client:
            get_client.return_value.read_tuples.return_value = stored
            out = StringIO()
            call_command("rebac_reconcile", "MockFolder", stdout=out)

        value = out.getvalue()
        assert "DRIFT folder:1" in value
        assert "missing    (WRITE)    user:alice owner" in value
        assert "unexpected (DELETE)   user:bob viewer" in value
        # Report-only by default: nothing is enqueued.
        assert RebacSyncOutbox.objects.count() == 0

    def test_apply_queues_repairs_through_outbox(self, folder):
        stored = [
            {"user": "organization:org-1", "relation": "organization", "object": "folder:1"},
            {"user": "user:bob", "relation": "viewer", "object": "folder:1"},
        ]
        with patch("rebac.management.commands.rebac_reconcile.get_rebac_client") as get_client:
            get_client.return_value.read_tuples.return_value = stored
            out = StringIO()
            call_command("rebac_reconcile", "MockFolder", "--apply", "--no-sync", stdout=out)

        assert _pending() == [
            (RebacSyncOutbox.Action.WRITE, "user:alice", "owner", "folder:1"),
            (RebacSyncOutbox.Action.DELETE, "user:bob", "viewer", "folder:1"),
        ]
        assert "2 outbox row(s) enqueued (1 WRITE, 1 DELETE)" in out.getvalue()

    def test_aborts_on_connection_error(self, folder):
        with (
            patch("rebac.management.commands.rebac_reconcile.get_rebac_client") as get_client,
            pytest.raises(CommandError, match="Reconcile aborted"),
        ):
            get_client.return_value.read_tuples.side_effect = RebacConnectionError("down")
            call_command("rebac_reconcile", "MockFolder", "--apply")

        # An unverifiable store must not produce a partial, half-trusted repair set.
        assert RebacSyncOutbox.objects.count() == 0

    def test_rejects_non_rebac_model(self, folder):
        with pytest.raises(CommandError, match="RebacModelSyncMixin"):
            call_command("rebac_reconcile", "Invoice")
