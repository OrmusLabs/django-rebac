# tests/test_tasks.py
import pytest

from rebac.models import RebacSyncOutbox
from rebac.tasks import process_rebac_outbox_batch

pytestmark = pytest.mark.django_db


class TestProcessOutboxBatch:
    def test_successful_batch_sync(self, mock_rebac_client):
        """Verifies pending tasks are gathered, sent to the ReBAC backend, and marked as Synced."""
        # Create dummy pending tasks
        RebacSyncOutbox.objects.create(
            action=RebacSyncOutbox.Action.WRITE,
            user_id="user:1",
            relation="viewer",
            object_id="doc:1",
        )
        RebacSyncOutbox.objects.create(
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

    def test_flip_flop_resolves_to_latest_state(self, mock_rebac_client):
        """T2.5: grant -> revoke -> re-grant on the same tuple collapses to latest intent.

        Regression: the old bucketing split a batch into writes-then-deletes, so a
        re-grant that should win was applied before its matching revoke and the delete
        removed the grant. Latest-state resolution keeps the final state correct.
        """
        o = RebacSyncOutbox
        o.objects.create(
            action=o.Action.DELETE, user_id="user:alice", relation="owner", object_id="folder:1"
        )
        o.objects.create(
            action=o.Action.WRITE, user_id="user:bob", relation="owner", object_id="folder:1"
        )
        o.objects.create(
            action=o.Action.DELETE, user_id="user:bob", relation="owner", object_id="folder:1"
        )
        o.objects.create(
            action=o.Action.WRITE, user_id="user:alice", relation="owner", object_id="folder:1"
        )

        process_rebac_outbox_batch()

        writes = mock_rebac_client.write_tuples.call_args[0][0]
        deletes = mock_rebac_client.delete_tuples.call_args[0][0]
        assert writes == [{"user": "user:alice", "relation": "owner", "object": "folder:1"}]
        assert deletes == [{"user": "user:bob", "relation": "owner", "object": "folder:1"}]

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

    def test_failure_only_penalizes_the_claimed_batch(self, mock_rebac_client, mocker, settings):
        """A failed batch must not charge a retry against rows it never processed.

        Regression test: the failure handler used to re-query for PENDING rows instead of
        using the batch it had claimed, so any row that happened to be pending at failure
        time was penalized -- and eventually marked FAILED -- despite never being sent.
        """
        from celery.exceptions import Retry

        settings.REBAC_CONFIG = {**settings.REBAC_CONFIG, "BATCH_SIZE": 1}

        rows = {
            "doc:1": RebacSyncOutbox.objects.create(
                action=RebacSyncOutbox.Action.WRITE,
                user_id="user:1",
                relation="viewer",
                object_id="doc:1",
            ),
            "doc:2": RebacSyncOutbox.objects.create(
                action=RebacSyncOutbox.Action.WRITE,
                user_id="user:2",
                relation="viewer",
                object_id="doc:2",
            ),
        }

        mock_rebac_client.write_tuples.side_effect = Exception("Backend Server Down")
        mocker.patch("rebac.tasks.process_rebac_outbox_batch.retry", side_effect=Retry)

        with pytest.raises(Retry):
            process_rebac_outbox_batch()

        # BATCH_SIZE=1, so exactly one row was claimed and sent. Read which one off the
        # call args rather than assuming an ordering between two identical timestamps.
        sent = mock_rebac_client.write_tuples.call_args[0][0]
        assert len(sent) == 1
        claimed = rows.pop(sent[0]["object"])
        (untouched,) = rows.values()

        claimed.refresh_from_db()
        assert claimed.retry_count == 1

        # The row that was never sent must not be charged a failed attempt.
        untouched.refresh_from_db()
        assert untouched.retry_count == 0
        assert untouched.status == RebacSyncOutbox.Status.PENDING

    def test_max_retries_is_read_at_runtime_not_import_time(
        self, mock_rebac_client, mocker, settings
    ):
        """A settings override for MAX_RETRIES must take effect without re-importing.

        Regression test: MAX_RETRIES was baked into the @shared_task decorator, so it was
        resolved once at import time and could never be changed afterwards.
        """
        from celery.exceptions import Retry

        settings.REBAC_CONFIG = {**settings.REBAC_CONFIG, "MAX_RETRIES": 2}

        task1 = RebacSyncOutbox.objects.create(
            action=RebacSyncOutbox.Action.WRITE,
            user_id="user:1",
            relation="viewer",
            object_id="doc:1",
            retry_count=1,  # With MAX_RETRIES=2, this attempt is the last one.
        )

        mock_rebac_client.write_tuples.side_effect = Exception("Backend Server Down")
        retry_mock = mocker.patch(
            "rebac.tasks.process_rebac_outbox_batch.retry",
            side_effect=Retry,
        )

        with pytest.raises(Retry):
            process_rebac_outbox_batch()

        task1.refresh_from_db()
        assert task1.retry_count == 2
        assert task1.status == RebacSyncOutbox.Status.FAILED
        assert retry_mock.call_args.kwargs["max_retries"] == 2
