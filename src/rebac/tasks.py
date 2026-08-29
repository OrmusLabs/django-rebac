# rebac/tasks.py
import logging
from typing import Any

from celery import shared_task
from django.db import transaction

from rebac.conf import get_setting
from rebac.models import RebacSyncOutbox
from rebac.utils import get_rebac_client

logger = logging.getLogger(__name__)


def _claim_pending_batch() -> list[RebacSyncOutbox]:
    """Locks and returns the next batch of pending outbox rows.

    Must be called inside a transaction: `select_for_update(skip_locked=True)` holds the
    row locks until commit, which is what stops two workers from claiming the same rows.

    Returns:
        list[RebacSyncOutbox]: The claimed rows, up to `BATCH_SIZE`.
    """
    return list(
        RebacSyncOutbox.objects.select_for_update(skip_locked=True).filter(
            status=RebacSyncOutbox.Status.PENDING
        )[: get_setting("BATCH_SIZE")]
    )


def _sync_batch(pending_tasks: list[RebacSyncOutbox]) -> str:
    """Pushes one claimed batch to the configured ReBAC backend and marks it synced.

    Tuples are resolved to each key's *latest* intended state before any network call:
    for every ``(user, relation, object)`` key only the most recently enqueued row
    survives. This collapses a ``grant -> revoke -> re-grant`` flip-flop into a single
    correct write, and -- combined with the idempotent backend contract (T2.6) -- keeps
    outbox replays correct regardless of how the rows were partitioned.

    Args:
        pending_tasks: The rows claimed by :func:`_claim_pending_batch`, in enqueue order.

    Returns:
        str: A human-readable summary of the work performed.
    """
    rebac_client = get_rebac_client()

    # Keep only the newest outbox row per tuple key. `pending_tasks` arrives in enqueue
    # order (created_at, id), so later rows overwrite earlier ones for the same key.
    latest: dict[tuple[str, str, str], RebacSyncOutbox] = {}
    for task in pending_tasks:
        latest[task.user_id, task.relation, task.object_id] = task

    writes: list[dict[str, str]] = []
    deletes: list[dict[str, str]] = []
    for task in latest.values():
        tuple_dict = {
            "user": task.user_id,
            "relation": task.relation,
            "object": task.object_id,
        }
        if task.action == RebacSyncOutbox.Action.WRITE:
            writes.append(tuple_dict)
        elif task.action == RebacSyncOutbox.Action.DELETE:
            deletes.append(tuple_dict)

    # Delegate mutation to the configured backend (e.g., OpenFGA, SpiceDB)
    if writes:
        rebac_client.write_tuples(writes)
    if deletes:
        rebac_client.delete_tuples(deletes)

    # Fast bulk update of status
    task_ids = [t.id for t in pending_tasks]
    RebacSyncOutbox.objects.filter(id__in=task_ids).update(status=RebacSyncOutbox.Status.SYNCED)

    return f"Successfully synced {len(pending_tasks)} ReBAC tuples."


def _record_batch_failure(pending_tasks: list[RebacSyncOutbox], max_retries: int) -> None:
    """Charges a failed attempt against exactly the rows that were being processed.

    Scoping this to the claimed batch matters: re-querying for `PENDING` rows here would
    penalize whatever happens to be pending at failure time, which under concurrent
    workers is not the batch that failed.

    Args:
        pending_tasks: The rows that failed to sync.
        max_retries: Attempt ceiling, after which a row is marked `FAILED`.
    """
    for task in pending_tasks:
        task.retry_count += 1
        if task.retry_count >= max_retries:
            task.status = RebacSyncOutbox.Status.FAILED
        task.save(update_fields=["retry_count", "status"])

    # T2.7: a FAILED row is permanent divergence. Emit one structured ERROR line per
    # batch (not per row) that log aggregation can key on, pointing at the two repair
    # paths — detection must never depend on a user complaint.
    failed_ids = [t.id for t in pending_tasks if t.status == RebacSyncOutbox.Status.FAILED]
    if failed_ids:
        logger.error(
            "ReBAC outbox: %d row(s) permanently FAILED after %d retries: %s. "
            "Repair: fix the root cause, then requeue via the admin action or run "
            "`python manage.py rebac_reconcile --apply`.",
            len(failed_ids),
            max_retries,
            failed_ids[:20],
        )


@shared_task(bind=True)
def process_rebac_outbox_batch(self: Any) -> str:
    """
    Process a batch of pending ReBAC synchronization tasks from the outbox.

    This task implements the outbox pattern for reliable async synchronization
    with ReBAC backend. It safely handles concurrent workers through row-level locking
    and provides exponential backoff retry logic on failures.

    The workflow:
    1. Lock and fetch pending tasks (skip_locked prevents worker contention)
    2. Batch tasks into WRITE/DELETE operations
    3. Send batch request to ReBAC backend
    4. Update task statuses to SYNCED on success
    5. On failure: increment retry_count, mark as FAILED if max retries exceeded
    6. Retry with exponential backoff (2^retries seconds)

    Returns:
        str: Status message indicating number of tasks processed or reason for no-op

    Raises:
        Retry: Celery retry exception if batch processing fails (after DB commit)

    Note:
        - Uses select_for_update(skip_locked=True) to prevent multiple workers
          from processing the same tasks
        - BATCH_SIZE and MAX_RETRIES are configurable via settings, read on each run
          rather than at import time
        - Failed tasks are marked FAILED after MAX_RETRIES attempts
        - The retry is raised outside the atomic block so the retry bookkeeping is
          committed before the broker can hand the task to another worker
    """
    max_retries = get_setting("MAX_RETRIES")
    retry_exc: Exception | None = None

    with transaction.atomic():
        pending_tasks = _claim_pending_batch()

        if not pending_tasks:
            return "No pending tasks."

        try:
            return _sync_batch(pending_tasks)
        except Exception as e:
            logger.error(f"ReBAC Sync operation failed: {e}")
            _record_batch_failure(pending_tasks, max_retries)

            # Store the exception to raise it AFTER the DB commits
            retry_exc = e

    # Safely tell the Celery broker to retry the task outside the atomic block
    countdown = 2**self.request.retries
    raise self.retry(exc=retry_exc, countdown=countdown, max_retries=max_retries)
