# rebac/services.py
from django.db import transaction

from .models import RebacSyncOutbox
from .tasks import process_rebac_outbox_batch


class RebacTupleIngestionService:
    """Service for consuming external events (like RabbitMQ or Redis) into the ReBAC Outbox."""

    @staticmethod
    def queue_tuple(action: str, user: str, relation: str, rebac_object: str) -> None:
        """Queues an external relationship tuple into the transactional outbox.

        Args:
            action: The operation type, e.g., RebacSyncOutbox.Action.WRITE.
            user: The subject identifier string.
            relation: The relationship/role string.
            rebac_object: The object identifier string.
        """
        with transaction.atomic():
            RebacSyncOutbox.objects.create(
                action=action, user_id=user, relation=relation, object_id=rebac_object
            )
            # Trigger the celery worker just like the mixin does
            transaction.on_commit(lambda: process_rebac_outbox_batch.delay())
