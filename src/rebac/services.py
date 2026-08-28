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
