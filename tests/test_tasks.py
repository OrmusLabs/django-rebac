# tests/test_tasks.py
from datetime import timedelta

import pytest
from django.utils import timezone

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

    # --- T2.8: no network I/O inside a locked transaction ---------------------

    def test_claim_is_committed_in_flight_before_network_call(self, mock_rebac_client):
        """T2.8: at the moment the network call runs, the claimed rows are already
        IN_FLIGHT in the database (transaction #1 committed, locks released) —
        not still PENDING rows held under open row locks."""
        row = RebacSyncOutbox.objects.create(
            action=RebacSyncOutbox.Action.WRITE,
            user_id="user:1",
            relation="viewer",
            object_id="doc:1",
        )

        observed: dict[str, str] = {}

        def observe(*args, **kwargs):
            observed["status"] = RebacSyncOutbox.objects.get(pk=row.pk).status

        mock_rebac_client.write_tuples.side_effect = observe

        result = process_rebac_outbox_batch()

        assert result == "Successfully synced 1 ReBAC tuples."
        # Under the old single-transaction design this was still PENDING here.
        assert observed["status"] == RebacSyncOutbox.Status.IN_FLIGHT
        row.refresh_from_db()
        assert row.status == RebacSyncOutbox.Status.SYNCED
        assert row.claimed_at is not None

    def test_fresh_in_flight_rows_are_not_stolen_by_another_drain(self, mock_rebac_client):
        """T2.8: a row claimed by a live worker (IN_FLIGHT, fresh claim) must not
        be reclaimed — only the IN_FLIGHT_TIMEOUT window releases it."""
        row = RebacSyncOutbox.objects.create(
            action=RebacSyncOutbox.Action.WRITE,
            user_id="user:1",
            relation="viewer",
            object_id="doc:1",
        )
        RebacSyncOutbox.objects.filter(pk=row.pk).update(
            status=RebacSyncOutbox.Status.IN_FLIGHT,
            claimed_at=timezone.now(),
        )

        result = process_rebac_outbox_batch()

        assert result == "No pending tasks."
        mock_rebac_client.write_tuples.assert_not_called()
        row.refresh_from_db()
        assert row.status == RebacSyncOutbox.Status.IN_FLIGHT

    def test_stale_in_flight_rows_are_reaped_and_synced(self, mock_rebac_client):
        """T2.8: a worker died mid-call and left a row IN_FLIGHT. The next drain
        reaps the stale claim and completes the sync — at-least-once, no loss."""
        row = RebacSyncOutbox.objects.create(
            action=RebacSyncOutbox.Action.WRITE,
            user_id="user:1",
            relation="viewer",
            object_id="doc:1",
        )
        # Default IN_FLIGHT_TIMEOUT is 300s; age the claim past it.
        stale = timezone.now() - timedelta(seconds=301)
        RebacSyncOutbox.objects.filter(pk=row.pk).update(
            status=RebacSyncOutbox.Status.IN_FLIGHT,
            claimed_at=stale,
        )

        result = process_rebac_outbox_batch()

        assert result == "Successfully synced 1 ReBAC tuples."
        mock_rebac_client.write_tuples.assert_called_once()
        row.refresh_from_db()
        assert row.status == RebacSyncOutbox.Status.SYNCED

    def test_reap_window_is_configurable(self, mock_rebac_client, settings):
        """T2.8: IN_FLIGHT_TIMEOUT bounds the reap window and is read at runtime."""
        settings.REBAC_CONFIG = {**settings.REBAC_CONFIG, "IN_FLIGHT_TIMEOUT": 1}

        row = RebacSyncOutbox.objects.create(
            action=RebacSyncOutbox.Action.WRITE,
            user_id="user:1",
            relation="viewer",
            object_id="doc:1",
        )
        # 2s old > 1s window → reapable, even though the 300s default would not be.
        stale = timezone.now() - timedelta(seconds=2)
        RebacSyncOutbox.objects.filter(pk=row.pk).update(
            status=RebacSyncOutbox.Status.IN_FLIGHT,
            claimed_at=stale,
        )

        process_rebac_outbox_batch()

        mock_rebac_client.write_tuples.assert_called_once()
        row.refresh_from_db()
        assert row.status == RebacSyncOutbox.Status.SYNCED

    def test_stale_in_flight_rows_count_against_max_retries(self, mock_rebac_client, mocker):
        """T2.8: a reclaimed stale row whose resend then fails still counts against
        MAX_RETRIES, so the reaper can never loop a row forever."""
        from celery.exceptions import Retry

        row = RebacSyncOutbox.objects.create(
            action=RebacSyncOutbox.Action.WRITE,
            user_id="user:1",
            relation="viewer",
            object_id="doc:1",
            retry_count=4,  # One attempt left (default MAX_RETRIES is 5).
        )
        stale = timezone.now() - timedelta(seconds=301)
        RebacSyncOutbox.objects.filter(pk=row.pk).update(
            status=RebacSyncOutbox.Status.IN_FLIGHT,
            claimed_at=stale,
        )

        mock_rebac_client.write_tuples.side_effect = Exception("Backend Server Down")
        mocker.patch("rebac.tasks.process_rebac_outbox_batch.retry", side_effect=Retry)

        with pytest.raises(Retry):
            process_rebac_outbox_batch()

        row.refresh_from_db()
        assert row.retry_count == 5
        assert row.status == RebacSyncOutbox.Status.FAILED

    def test_failure_bookkeeping_does_not_call_per_row_save(
        self, mock_rebac_client, mocker, settings
    ):
        """T2.8 (minor): the failure path charges N rows with two bulk UPDATEs,
        not one save() per row."""
        from celery.exceptions import Retry

        settings.REBAC_CONFIG = {**settings.REBAC_CONFIG, "BATCH_SIZE": 5}
        rows = [
            RebacSyncOutbox.objects.create(
                action=RebacSyncOutbox.Action.WRITE,
                user_id=f"user:{i}",
                relation="viewer",
                object_id=f"doc:{i}",
            )
            for i in range(3)
        ]

        mock_rebac_client.write_tuples.side_effect = Exception("Backend Server Down")
        mocker.patch("rebac.tasks.process_rebac_outbox_batch.retry", side_effect=Retry)
        save_mock = mocker.patch.object(RebacSyncOutbox, "save")

        with pytest.raises(Retry):
            process_rebac_outbox_batch()

        # The old implementation did task.save() once per failed row.
        save_mock.assert_not_called()
        for row in rows:
            row.refresh_from_db()
            assert row.retry_count == 1
            assert row.status == RebacSyncOutbox.Status.PENDING
