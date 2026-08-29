# rebac/services.py
import logging

from django.db import transaction

from .conf import get_setting
from .models import RebacSyncOutbox

logger = logging.getLogger(__name__)


class RebacTupleIngestionService:
    """Service for consuming external and internal events into the ReBAC Outbox."""

    @staticmethod
    def queue_tuple(
        action: str | RebacSyncOutbox.Action,
        user: str,
        relation: str,
        rebac_object: str,
        trigger_worker: bool = True,
    ) -> None:
        """Queues a relationship tuple into the transactional outbox.

        Args:
            action: The operation type (e.g., RebacSyncOutbox.Action.WRITE).
            user: The subject identifier string.
            relation: The relationship/role string.
            rebac_object: The object identifier string.
            trigger_worker: If True, schedules the Celery worker immediately on commit.
        """
        action_value: str = action.value if hasattr(action, "value") else str(action)

        with transaction.atomic():
            RebacSyncOutbox.objects.create(
                action=action_value,
                user_id=user,
                relation=relation,
                object_id=rebac_object,
            )

            if trigger_worker:
                RebacTupleIngestionService.trigger_sync()

    @staticmethod
    def queue_tuples(tuples: list[dict[str, str]], trigger_worker: bool = True) -> int:
        """Bulk-enqueues tuples into the outbox with a single ``bulk_create`` (T2.7).

        Use this instead of N x `queue_tuple` for large batches (backfills, big
        cascades): one INSERT instead of N, and no per-row savepoint. Pairs with the
        idempotent write contract — replaying the same tuples is a no-op.

        Args:
            tuples: List of dicts, each with 'action' (WRITE/DELETE), 'user',
                'relation', and 'object' — the same shape `queue_tuple` accepts.
            trigger_worker: If True, schedules the Celery worker once on commit.

        Returns:
            int: Number of outbox rows created (0 for an empty list).
        """
        if not tuples:
            return 0

        rows = [
            RebacSyncOutbox(
                action=t["action"].value if hasattr(t["action"], "value") else t["action"],
                user_id=t["user"],
                relation=t["relation"],
                object_id=t["object"],
            )
            for t in tuples
        ]

        with transaction.atomic():
            created = RebacSyncOutbox.objects.bulk_create(rows)
            if trigger_worker:
                RebacTupleIngestionService.trigger_sync()

        return len(created)

    @staticmethod
    def trigger_sync() -> None:
        """Safely schedules the outbox drain after the transaction commits.

        The outbox row is already durable when this runs, so the dispatch is
        best-effort: if the broker (or the in-process runner) is unavailable we log
        and rely on the periodic sweeper to drain the row. Losing the immediate
        dispatch costs latency, never correctness -- a request must never 500 on an
        operation that already committed.

        ``SYNC_MODE`` selects the dispatch strategy:
            - ``"ASYNC"`` (default): enqueue a Celery task (``.delay()``).
            - ``"INLINE"``: run the drain in-process (``.apply()``) so the tuple is
              visible immediately -- ideal for local development and CI read-after-write.
        """
        from .tasks import process_rebac_outbox_batch

        sync_mode = get_setting("SYNC_MODE")
        dispatch = (
            process_rebac_outbox_batch.apply
            if sync_mode == "INLINE"
            else process_rebac_outbox_batch.delay
        )

        def _safe_dispatch() -> None:
            try:
                dispatch()
            # A broker hiccup is recoverable via the periodic sweeper, so swallow it:
            # the outbox row is already durable and losing this dispatch costs latency,
            # never correctness.
            except Exception:
                logger.warning(
                    "ReBAC outbox dispatch failed (SYNC_MODE=%s); the periodic "
                    "sweeper will drain the pending rows.",
                    sync_mode,
                    exc_info=True,
                )

        transaction.on_commit(_safe_dispatch)
