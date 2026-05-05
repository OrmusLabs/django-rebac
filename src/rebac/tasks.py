# rebac/tasks.py
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from celery import shared_task
from django.db import transaction

from rebac.conf import get_setting
from rebac.models import RebacSyncOutbox
from rebac.utils import get_rebac_client

logger = logging.getLogger(__name__)


def rebac_retry_on_failure(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to handle retry logic for ReBAC sync operations.

    This decorator ensures that database updates (retry counts, status changes)
    are committed BEFORE triggering Celery retries. This prevents race conditions
    where a retry might execute before the database reflects the previous attempt.

    Args:
        func: The Celery task function to wrap.

    Returns:
        Callable: Wrapped function with safe retry behavior.
    """

    @wraps(func)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        retry_exc = None  # Capture exception to raise AFTER transaction commits

        with transaction.atomic():
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                logger.error(f"ReBAC Sync operation failed: {e}")

                # Get pending tasks from the outbox to update their retry counts
                pending_tasks = list(
                    RebacSyncOutbox.objects.select_for_update(skip_locked=True).filter(
                        status=RebacSyncOutbox.Status.PENDING
                    )[: get_setting("BATCH_SIZE")]
                )

                # Update retry counts for all pending tasks
                for task in pending_tasks:
                    task.retry_count += 1
                    if task.retry_count >= self.max_retries:
                        task.status = RebacSyncOutbox.Status.FAILED
                    task.save(update_fields=["retry_count", "status"])

                # Store the exception to raise it AFTER the DB commits
                retry_exc = e

        # Safely tell the Celery broker to retry the task outside the atomic block
        if retry_exc:
            countdown = 2**self.request.retries
            raise self.retry(exc=retry_exc, countdown=countdown)

    return wrapper


@shared_task(bind=True, max_retries=get_setting("MAX_RETRIES"))
@rebac_retry_on_failure
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
        - BATCH_SIZE and MAX_RETRIES are configurable via settings
        - Failed tasks are marked FAILED after max_retries attempts
    """
    pending_tasks = list(
        RebacSyncOutbox.objects.select_for_update(skip_locked=True).filter(
            status=RebacSyncOutbox.Status.PENDING
        )[: get_setting("BATCH_SIZE")]
    )

    if not pending_tasks:
        return "No pending tasks."

    rebac_client = get_rebac_client()
    writes: list[dict[str, str]] = []
    deletes: list[dict[str, str]] = []

    # Map database outbox models into abstract dictionaries
    for task in pending_tasks:
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
