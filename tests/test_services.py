# tests/test_services.py
from unittest.mock import patch

import pytest

from rebac.models import RebacSyncOutbox
from rebac.services import RebacTupleIngestionService

# transaction=True is required for testing transaction.on_commit() hooks!
# Without this, pytest rolls back the database before the commit hook ever fires.
pytestmark = pytest.mark.django_db(transaction=True)


class TestRebacTupleIngestionService:
    @patch("rebac.tasks.process_rebac_outbox_batch.delay")
    def test_queue_tuple_creates_record_and_triggers_task(self, mock_delay):
        """
        Verifies that consuming an external event safely writes to the Outbox
        and immediately notifies the Celery worker.
        """
        # Ensure outbox is empty before test
        RebacSyncOutbox.objects.all().delete()

        # Execute the Service logic
        RebacTupleIngestionService.queue_tuple(
            action=RebacSyncOutbox.Action.WRITE,
            user="user:external_worker_99",
            relation="viewer",
            rebac_object="document:777",
        )

        # 1. Mathematical Proof of Database Integrity
        # Assert the record was saved flawlessly
        assert RebacSyncOutbox.objects.count() == 1

        task = RebacSyncOutbox.objects.get()
        assert task.action == RebacSyncOutbox.Action.WRITE
        assert task.user_id == "user:external_worker_99"
        assert task.relation == "viewer"
        assert task.object_id == "document:777"
        assert task.status == RebacSyncOutbox.Status.PENDING

        # 2. Mathematical Proof of Async Trigger
        # Assert that the Celery task was successfully queued upon transaction commit
        mock_delay.assert_called_once()

    @pytest.mark.parametrize(
        ("sync_mode", "expected", "unexpected"),
        [("INLINE", "apply", "delay"), ("ASYNC", "delay", "apply")],
    )
    def test_trigger_sync_dispatches_by_mode(
        self, settings, sync_mode: str, expected: str, unexpected: str
    ):
        """T1.4: SYNC_MODE selects the dispatch strategy (INLINE=.apply, ASYNC=.delay)."""
        settings.REBAC_CONFIG = {**settings.REBAC_CONFIG, "SYNC_MODE": sync_mode}
        RebacSyncOutbox.objects.all().delete()

        with (
            patch(f"rebac.tasks.process_rebac_outbox_batch.{expected}") as called,
            patch(f"rebac.tasks.process_rebac_outbox_batch.{unexpected}") as not_called,
        ):
            RebacTupleIngestionService.queue_tuple(
                action=RebacSyncOutbox.Action.WRITE,
                user="user:x",
                relation="viewer",
                rebac_object="d:1",
            )

        called.assert_called_once()
        not_called.assert_not_called()

    def test_dispatch_failure_is_swallowed(self, settings):
        """T1.4: a broker failure must not 500 a request that already committed."""
        settings.REBAC_CONFIG = {**settings.REBAC_CONFIG, "SYNC_MODE": "ASYNC"}
        RebacSyncOutbox.objects.all().delete()

        with patch(
            "rebac.tasks.process_rebac_outbox_batch.delay",
            side_effect=RuntimeError("broker down"),
        ):
            # Must NOT raise, even though the commit-hook dispatch fails.
            RebacTupleIngestionService.queue_tuple(
                action=RebacSyncOutbox.Action.WRITE,
                user="user:x",
                relation="viewer",
                rebac_object="d:1",
            )

        # The outbox row is durable regardless of the dispatch failure.
        assert RebacSyncOutbox.objects.count() == 1
