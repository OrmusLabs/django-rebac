# rebac/services.py
from django.db import transaction

from .models import RebacSyncOutbox


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
        """Safely schedules the celery worker after the transaction commits."""
        from .tasks import process_rebac_outbox_batch

        transaction.on_commit(lambda: process_rebac_outbox_batch.delay())
