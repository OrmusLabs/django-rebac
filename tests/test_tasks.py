# tests/test_tasks.py
import pytest

from rebac.models import RebacSyncOutbox
from rebac.tasks import process_rebac_outbox_batch

pytestmark = pytest.mark.django_db


class TestProcessOutboxBatch:
    def test_successful_batch_sync(self, mock_rebac_client):
        """Verifies pending tasks are gathered, sent to the ReBAC backend, and marked as Synced."""
        # Create dummy pending tasks
        task1 = RebacSyncOutbox.objects.create(
            action=RebacSyncOutbox.Action.WRITE,
            user_id="user:1",
            relation="viewer",
            object_id="doc:1",
        )
        task2 = RebacSyncOutbox.objects.create(
            action=RebacSyncOutbox.Action.DELETE,
            user_id="user:2",
            relation="editor",
            object_id="doc:2",
        )

        # Execute the Celery task synchronously
        result = process_rebac_outbox_batch()

        # 1. Verify the task output (Updated to "ReBAC")
        assert result == "Successfully synced 2 ReBAC tuples."

        # 2. Verify the SDK was called with the correct formatted payloads
        # Check the new agnostic methods
        mock_rebac_client.write_tuples.assert_called_once()
        mock_rebac_client.delete_tuples.assert_called_once()

        # Assert against raw dictionaries instead of OpenFGA objects
        writes = mock_rebac_client.write_tuples.call_args[0][0]
        assert len(writes) == 1
        assert writes[0]["user"] == "user:1"
        assert writes[0]["relation"] == "viewer"
        assert writes[0]["object"] == "doc:1"

        deletes = mock_rebac_client.delete_tuples.call_args[0][0]
        assert len(deletes) == 1
        assert deletes[0]["user"] == "user:2"
        assert deletes[0]["relation"] == "editor"
        assert deletes[0]["object"] == "doc:2"

    def test_batch_sync_failure_and_retry(self, mock_rebac_client, mocker):
        """Verifies that a network failure triggers a Celery retry and updates retry_count."""
        from celery.exceptions import Retry

        task1 = RebacSyncOutbox.objects.create(
            action=RebacSyncOutbox.Action.WRITE,
            user_id="user:1",
            relation="viewer",
            object_id="doc:1",
        )

        # 1. Force the agnostic backend mock to throw a Network Exception
        # 🛠️ THE FIX: Target write_tuples, not write
        mock_rebac_client.write_tuples.side_effect = Exception("Backend Server Down")

        # 2. Intercept Celery's retry mechanism so we can catch the exception safely
        mocker.patch("rebac.tasks.process_rebac_outbox_batch.retry", side_effect=Retry)

        with pytest.raises(Retry):
            process_rebac_outbox_batch()

        # 3. Verify the database recorded the failed attempt
        task1.refresh_from_db()
        assert task1.retry_count == 1
        assert task1.status == RebacSyncOutbox.Status.PENDING  # Still pending, will retry later

    def test_batch_sync_max_retries_failure(self, mock_rebac_client, mocker):
        """Verifies that exceeding max retries sets the status to FAILED."""
        from celery.exceptions import Retry

        task1 = RebacSyncOutbox.objects.create(
            action=RebacSyncOutbox.Action.WRITE,
            user_id="user:1",
            relation="viewer",
            object_id="doc:1",
            retry_count=4,  # Max retries is 5, so this attempt will be the last one
        )

        # Target write_tuples, not write
        mock_rebac_client.write_tuples.side_effect = Exception("Backend Server Down")
        mocker.patch("rebac.tasks.process_rebac_outbox_batch.retry", side_effect=Retry)

        with pytest.raises(Retry):
            process_rebac_outbox_batch()

        # The task gave up, so status should now permanently be FAILED
        task1.refresh_from_db()
        assert task1.retry_count == 5
        assert task1.status == RebacSyncOutbox.Status.FAILED
