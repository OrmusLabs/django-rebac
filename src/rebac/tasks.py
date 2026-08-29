# rebac/tasks.py
import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from rebac.conf import get_setting
from rebac.models import RebacSyncOutbox
from rebac.utils import get_rebac_client

logger = logging.getLogger(__name__)


def _claimable_outbox(inflight_timeout: float) -> Q:
    """T2.8: the set of outbox rows a drain may claim.

    PENDING rows are always claimable. IN_FLIGHT rows are claimable only once
    their claim has gone stale: the worker that claimed them died (OOM kill,
    crash, restart) before it could mark the row SYNCED or return it to
    PENDING. This reaper is what makes the claim-and-commit design below
    at-least-once — a dead worker can no longer leave a row stuck forever.
    """
    stale_before = timezone.now() - timedelta(seconds=inflight_timeout)
    return Q(status=RebacSyncOutbox.Status.PENDING) | Q(
        status=RebacSyncOutbox.Status.IN_FLIGHT, claimed_at__lt=stale_before
    )


def _claim_pending_batch() -> list[RebacSyncOutbox]:
    """Claims the next batch of outbox rows and marks them IN_FLIGHT.

    T2.8: this is transaction #1 of the drain. It locks the claimable rows
    (`select_for_update(skip_locked=True)`) and flips them to IN_FLIGHT; when
    the caller's transaction commits, the row locks are released. The durable
    IN_FLIGHT state — not open row locks — is what stops two workers from
    double-processing the same batch, and because it is committed *before* any
    network I/O, the reaper (`_claimable_outbox`) can reclaim the batch if
    this worker dies mid-call.

    Must be called inside a transaction.

    Returns:
        list[RebacSyncOutbox]: The claimed rows, up to `BATCH_SIZE`, in enqueue
            order. Empty when nothing is claimable.
    """
    pending_tasks = list(
        RebacSyncOutbox.objects.select_for_update(skip_locked=True).filter(
            _claimable_outbox(get_setting("IN_FLIGHT_TIMEOUT"))
        )[: get_setting("BATCH_SIZE")]
    )

    if not pending_tasks:
        return []

    # One bulk UPDATE for the whole batch: IN_FLIGHT + claim timestamp.
    claimed_ids = [t.id for t in pending_tasks]
    RebacSyncOutbox.objects.filter(id__in=claimed_ids).update(
        status=RebacSyncOutbox.Status.IN_FLIGHT,
        claimed_at=timezone.now(),
    )
    return pending_tasks


def _sync_batch(pending_tasks: list[RebacSyncOutbox]) -> None:
    """Pushes one claimed batch to the configured ReBAC backend.

    T2.8: this performs the network I/O and nothing else, and it MUST be
    called outside any database transaction: the claim (IN_FLIGHT) was
    already committed by transaction #1 with its row locks released, and the
    SYNCED transition happens in a separate short transaction in the caller.
    Holding locks and a pooled connection across the backend round trip is
    exactly what this module no longer does.

    Tuples are resolved to each key's *latest* intended state before any network call:
    for every ``(user, relation, object)`` key only the most recently enqueued row
    survives. This collapses a ``grant -> revoke -> re-grant`` flip-flop into a single
    correct write, and -- combined with the idempotent backend contract (T2.6) -- keeps
    outbox replays correct regardless of how the rows were partitioned.

    Args:
        pending_tasks: The rows claimed by :func:`_claim_pending_batch`, in enqueue order.
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

    # T2.8: the SYNCED transition lives in the caller's short success transaction
    # (transaction #2) — this function stays pure network I/O.


def _record_batch_failure(pending_tasks: list[RebacSyncOutbox], max_retries: int) -> None:
    """Charges a failed attempt against exactly the rows that were being processed.

    T2.8: this is transaction #2 (failure branch), run in its own short
    transaction — the claim's locks are gone by now. Rows still under the
    retry ceiling go back to PENDING; rows that hit the ceiling go to FAILED.
    Two bulk UPDATEs (one per outcome) replace the old per-row save() loop,
    so N failed rows cost two queries instead of N.

    Scoping this to the claimed batch matters: re-querying for `PENDING` rows here would
    penalize whatever happens to be pending at failure time, which under concurrent
    workers is not the batch that failed.

    Args:
        pending_tasks: The rows that failed to sync.
        max_retries: Attempt ceiling, after which a row is marked `FAILED`.
    """
    retriable_ids = [t.id for t in pending_tasks if t.retry_count + 1 < max_retries]
    exhausted_ids = [t.id for t in pending_tasks if t.retry_count + 1 >= max_retries]

    with transaction.atomic():
        if retriable_ids:
            (
                RebacSyncOutbox.objects.filter(id__in=retriable_ids).update(
                    status=RebacSyncOutbox.Status.PENDING,
                    retry_count=F("retry_count") + 1,
                )
            )
        if exhausted_ids:
            (
                RebacSyncOutbox.objects.filter(id__in=exhausted_ids).update(
                    status=RebacSyncOutbox.Status.FAILED,
                    retry_count=F("retry_count") + 1,
                )
            )

    # T2.7: a FAILED row is permanent divergence. Emit one structured ERROR line per
    # batch (not per row) that log aggregation can key on, pointing at the two repair
    # paths — detection must never depend on a user complaint.
    if exhausted_ids:
        logger.error(
            "ReBAC outbox: %d row(s) permanently FAILED after %d retries: %s. "
            "Repair: fix the root cause, then requeue via the admin action or run "
            "`python manage.py rebac_reconcile --apply`.",
            len(exhausted_ids),
            max_retries,
            exhausted_ids[:20],
        )


@shared_task(bind=True)
def process_rebac_outbox_batch(self: Any) -> str:
    """
    Process a batch of pending ReBAC synchronization tasks from the outbox.

    This task implements the outbox pattern for reliable async synchronization
    with ReBAC backend. It safely handles concurrent workers through a durable
    IN_FLIGHT claim and provides exponential backoff retry logic on failures.

    T2.8: the drain is three short transactions, not one long one — no row lock
    or pooled DB connection is ever held across the OpenFGA round trip:

    txn 1: lock + claim the batch, mark it IN_FLIGHT → COMMIT (locks released)
           (no transaction): send the batch to the ReBAC backend
    txn 2: success → mark SYNCED; failure → PENDING (+1 retry) or FAILED

    The workflow:
    1. Claim the batch (skip_locked prevents worker contention) and mark it
       IN_FLIGHT in its own committed transaction — if this worker then dies,
       the reaper reclaims the stale IN_FLIGHT rows on the next drain
    2. Resolve the batch into WRITE/DELETE operations (latest state per tuple)
    3. Send the batch request to the ReBAC backend, outside any transaction
    4. On success: mark the batch SYNCED (transaction #2)
    5. On failure: increment retry_count, mark as FAILED if max retries exceeded
    6. Retry with exponential backoff (2^retries seconds)

    Returns:
        str: Status message indicating number of tasks processed or reason for no-op

    Raises:
        Retry: Celery retry exception if batch processing fails (after DB commit)

    Note:
        - Uses select_for_update(skip_locked=True) only inside the short claim
          transaction; the durable IN_FLIGHT claim carries correctness after
        - BATCH_SIZE, MAX_RETRIES and IN_FLIGHT_TIMEOUT are configurable via
          settings, read on each run rather than at import time
        - Failed tasks are marked FAILED after MAX_RETRIES attempts
        - The retry is raised outside the atomic block so the retry bookkeeping is
          committed before the broker can hand the task to another worker
    """
    max_retries = get_setting("MAX_RETRIES")
    retry_exc: Exception | None = None

    # Transaction #1 — claim and mark IN_FLIGHT. Row locks die with this commit;
    # from then on the durable IN_FLIGHT state (plus the reaper) owns correctness.
    with transaction.atomic():
        pending_tasks = _claim_pending_batch()

    if not pending_tasks:
        return "No pending tasks."

    # Network I/O — deliberately OUTSIDE any transaction (T2.8).
    try:
        _sync_batch(pending_tasks)
    except Exception as e:
        logger.error(f"ReBAC Sync operation failed: {e}")
        # Transaction #2 (failure branch) — committed before the Celery retry.
        _record_batch_failure(pending_tasks, max_retries)
        # Store the exception to raise it AFTER the failure bookkeeping commits.
        retry_exc = e
    else:
        # Transaction #2 (success branch) — mark the claimed batch SYNCED.
        with transaction.atomic():
            task_ids = [t.id for t in pending_tasks]
            RebacSyncOutbox.objects.filter(id__in=task_ids).update(
                status=RebacSyncOutbox.Status.SYNCED
            )
        return f"Successfully synced {len(pending_tasks)} ReBAC tuples."

    # Safely tell the Celery broker to retry the task outside the atomic block
    countdown = 2**self.request.retries
    raise self.retry(exc=retry_exc, countdown=countdown, max_retries=max_retries)
