# rebac/mixins.py
import logging
import uuid
from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models.signals import pre_delete

from ..common.loggers import RebacConsoleLogger
from ..core.adapters import RebacTupleAdapter
from ..core.structs import RebacModelConfig
from .outbox import RebacSyncOutbox

__all__ = [
    "RebacModelSyncMixin",
]

logger = logging.getLogger(__name__)

dev_logger = RebacConsoleLogger(__name__)


class RebacModelSyncMixin:
    """Structure-agnostic mixin for synchronizing Django models to the ReBAC store via
        the Outbox pattern.

    This mixin intercepts the standard Django `save()` and `delete()` lifecycles.
    It utilizes the defined `RebacModelConfig` to calculate the exact ReBAC tuple
    differences (diffs) and safely queues them in the local database transaction.

    Attributes:
        rebac_config: The strict configuration class defining how this model maps
                    to the OpenFGA graph. Must be an instance of `RebacModelConfig`.
        pk: The primary key of the model instance.

    Example:
        **Basic Creator Ownership:**
        ```python
        from django.db import models
        from rebac.structs import RebacModelConfig, RebacCreatorConfig

        class Document(RebacModelSyncMixin, models.Model):
            title = models.CharField(max_length=255)
            creator_id = models.CharField(max_length=255)

            rebac_config = RebacModelConfig(
                object_type="document",
                creators=[
                    RebacCreatorConfig(
                        relation="editor",
                        local_field="creator_id"
                    )
                ]
            )
        ```

        **Parent Hierarchies (Cascading):**
        ```python
        from rebac.structs import RebacParentConfig

        class Folder(RebacModelSyncMixin, models.Model):
            name = models.CharField(max_length=255)
            org_id = models.CharField(max_length=255)
            creator_id = models.CharField(max_length=255)

            rebac_config = RebacModelConfig(
                object_type="folder",
                parents=[
                    RebacParentConfig(
                        relation="organization",
                        parent_type="organization",
                        local_field="org_id"
                    )
                ],
                creators=[
                    RebacCreatorConfig(
                        relation="owner",
                        local_field="creator_id"
                    )
                ]
            )
        ```

        **Custom Role Assignment (Escape Hatch):**
        If you need to assign ReBAC roles based on dynamic data state (like a boolean field),
        you can intercept `save()` and manually queue tuples into the Outbox:
        ```python
        from rebac.models import RebacSyncOutbox

        class Article(RebacModelSyncMixin, models.Model):
            title = models.CharField(max_length=255)
            is_public = models.BooleanField(default=False)

            rebac_config = RebacModelConfig(object_type="article")

            def save(self, *args, **kwargs):
                # 1. Let the mixin handle the standard config-based tuples first
                super().save(*args, **kwargs)

                # 2. Inject your custom, dynamic logic
                if self.is_public:
                    self._queue_outbox(
                        action=RebacSyncOutbox.Action.WRITE,
                        t={
                            "user": "user:*",  # OpenFGA wildcard for 'everyone'
                            "relation": "viewer",
                            "object": f"article:{self.pk}"
                        }
                    )
        ```

    Notes:
        **Limitations:**
        Because this mixin relies on intercepting the `save()` method for the
        Transactional Outbox pattern, standard Django bulk operations
        (e.g., `Document.objects.bulk_create()`) will bypass this mixin.
        You must save instances individually or trigger the outbox manually
        for bulk operations.
    """

    rebac_config: ClassVar[RebacModelConfig | None] = None
    pk: int | str | uuid.UUID | None

    @classmethod
    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Metaprogramming Hook: Executed automatically when a Django model inherits
        from this mixin. Wires up framework-level safety nets.
        """
        super().__init_subclass__(**kwargs)

        # Generate a unique dispatch_uid to prevent duplicate signal registration
        # if the module is reloaded (e.g., during tests or dev server reloads).
        dispatch_uid = f"{cls.__module__}.{cls.__name__}.rebac_auto_cascade"
        pre_delete.connect(cls._rebac_auto_cascade_handler, sender=cls, dispatch_uid=dispatch_uid)

    @classmethod
    def _rebac_auto_cascade_handler(cls, sender: type, instance: Any, **kwargs: Any) -> None:
        """
        Framework-level signal handler. Traps deletions that bypass the instance
        .delete() method (e.g., SQL cascades or Admin bulk deletes).
        """
        if not instance.pk or not getattr(instance, "rebac_config", None):  # pragma: no cover
            return

        # Defensive flag: If standard .delete() was already called, skip to avoid duplicates
        if getattr(instance, "_rebac_is_deleting", False):
            return

        instance._rebac_is_deleting = True

        try:
            config = instance._get_validated_config()
            tuples_to_delete = RebacTupleAdapter.generate_tuples(instance, config)
            for t in tuples_to_delete:
                instance._queue_outbox(RebacSyncOutbox.Action.DELETE, t)
        except Exception as e:  # pragma: no cover
            logger.error(
                "ReBAC framework signal failure for"
                f" {instance.__class__.__name__} {instance.pk}: {e}"
            )

    def _get_validated_config(self) -> RebacModelConfig:
        """
        Safely extracts and validates the RebacModelConfig.
        Centralizes validation to adhere strictly to DRY principles.

        Returns:
            RebacModelConfig: The validated configuration object.

        Raises:
            ImproperlyConfigured: If the configuration is missing or of the wrong type.
        """
        if not isinstance(self.rebac_config, RebacModelConfig):
            raise ImproperlyConfigured(
                f"'{self.__class__.__name__}' must define a 'rebac_config' attribute "
                f"of type 'RebacModelConfig'."
            )
        return self.rebac_config

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        # Fail fast on instantiation if misconfigured
        config = self._get_validated_config()

        # Delegate tuple generation to the adapter
        self._original_tuples = RebacTupleAdapter.generate_tuples(self, config) if self.pk else []
        self._rebac_task_scheduled = False

    def save(self, *args: Any, **kwargs: Any) -> None:
        is_new = bool(self._state.adding)  # type: ignore[attr-defined]

        with transaction.atomic():
            super().save(*args, **kwargs)  # type: ignore[misc]

            config = self._get_validated_config()
            current_tuples = RebacTupleAdapter.generate_tuples(self, config)

            if is_new:
                for t in current_tuples:
                    self._queue_outbox(RebacSyncOutbox.Action.WRITE, t)  # type: ignore[arg-type]
            else:
                to_delete, to_write = RebacTupleAdapter.compute_diffs(
                    self._original_tuples, current_tuples
                )

                for t in to_delete:
                    self._queue_outbox(RebacSyncOutbox.Action.DELETE, t)  # type: ignore[arg-type]

                for t in to_write:
                    self._queue_outbox(RebacSyncOutbox.Action.WRITE, t)  # type: ignore[arg-type]

            self._original_tuples = current_tuples

    def delete(self, *args: Any, **kwargs: Any) -> None:
        """Standard instance deletion override."""
        with transaction.atomic():
            config = self._get_validated_config()

            # 1. Set the flag so the auto-handler knows we already caught it
            self._rebac_is_deleting = True

            # 2. Queue the deletions
            for t in RebacTupleAdapter.generate_tuples(self, config):
                self._queue_outbox(RebacSyncOutbox.Action.DELETE, t)  # type: ignore[arg-type]

            # 3. Proceed with Django's native deletion
            super().delete(*args, **kwargs)  # type: ignore[misc]

    def _queue_outbox(self, action: str | RebacSyncOutbox.Action, t: dict[str, str]) -> None:
        """Delegates tuple queueing to the centralized ReBAC service layer."""
        from ..services import RebacTupleIngestionService

        RebacTupleIngestionService.queue_tuple(
            action=action,
            user=t["user"],
            relation=t["relation"],
            rebac_object=t["object"],
            trigger_worker=False,
        )

        if not self._rebac_task_scheduled:
            RebacTupleIngestionService.trigger_sync()
            self._rebac_task_scheduled = True
