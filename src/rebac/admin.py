# rebac/admin.py
import logging
from typing import Any

from django.contrib import admin, messages
from django.db.models.query import QuerySet
from django.http import HttpRequest

from .conf import get_setting
from .models import RebacSyncOutbox

logger = logging.getLogger(__name__)

# Conditionally register the admin based on the package settings
if get_setting("ENABLE_OUTBOX_ADMIN"):  # pragma: no cover

    @admin.register(RebacSyncOutbox)
    class RebacSyncOutboxAdmin(admin.ModelAdmin):
        """
        Robust admin interface for monitoring the ReBAC Transactional Outbox.
        Provides search, filtering, and recovery actions for failed sync operations.
        """

        list_display = (
            "id",
            "status",
            "action",
            "relation",
            "object_id",
            "user_id",
            "retry_count",
            "created_at",
            "claimed_at",
        )

        list_filter = (
            "status",
            "action",
            "created_at",
        )

        search_fields = (
            "object_id",
            "user_id",
            "relation",
        )

        readonly_fields = (
            "created_at",
            "updated_at",
            "claimed_at",
        )

        ordering = ("-created_at",)

        # Add the custom recovery action to the dropdown
        actions = ("requeue_failed_tasks",)

        @admin.action(description="Rescue: Re-queue selected FAILED tasks")
        def requeue_failed_tasks(
            self, request: HttpRequest, queryset: QuerySet[RebacSyncOutbox]
        ) -> None:
            """
            Safely resets FAILED tasks back to PENDING so the Celery worker
            can automatically retry them on its next sweep.
            """
            # Defensively ensure we only reset tasks that are actually in a FAILED state
            updated_count: int = queryset.filter(status=RebacSyncOutbox.Status.FAILED).update(
                status=RebacSyncOutbox.Status.PENDING, retry_count=0
            )

            self.message_user(
                request,
                f"Successfully re-queued {updated_count} failed ReBAC tasks.",
                messages.SUCCESS,
            )
            logger.info("Admin %s re-queued %d ReBAC outbox tasks.", request.user, updated_count)

        def changelist_view(self, request: HttpRequest, extra_context: Any | None = None):
            """Alert on a FAILED backlog on every admin visit.

            A FAILED row is permanent divergence; surfacing the count as a banner
            (instead of relying on a human scrolling rows) is the admin-side half of
            the detection story.
            """
            failed_count = (
                self.get_queryset(request).filter(status=RebacSyncOutbox.Status.FAILED).count()
            )
            if failed_count:
                messages.error(
                    request,
                    f"ReBAC outbox: {failed_count} task(s) are permanently FAILED. "
                    "Fix the root cause, then use the 'Rescue: Re-queue selected FAILED "
                    "tasks' action or `python manage.py rebac_reconcile --apply`.",
                )
            return super().changelist_view(request, extra_context)

        def has_add_permission(self, request: HttpRequest) -> bool:
            """
            Defensive Guard: Prevent manual creation of outbox records via the UI.

            Outbox records MUST be created by the RebacModelSyncMixin during atomic
            database transactions to guarantee eventual consistency.
            """
            return False

        def has_change_permission(self, request: HttpRequest, obj: Any | None = None) -> bool:
            """
            Defensive Guard: Prevent manual editing of outbox records to maintain
            a strict audit trail of what was actually queued by the application.
            """
            return False
