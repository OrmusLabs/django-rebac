from django.db import models
from django.utils.translation import gettext_lazy as _


class RebacSyncOutbox(models.Model):
    class Status(models.TextChoices):
        # Database Value, Human Readable Label
        PENDING = "PEND", "⏳ " + _("Pending")
        # T2.8: claimed by a worker, sync in flight. Transient: becomes SYNCED,
        # returns to PENDING on failure, or is reaped by the next drain once
        # IN_FLIGHT_TIMEOUT elapses (the claiming worker died mid-call).
        IN_FLIGHT = "INFL", "🔄 " + _("In Flight")
        SYNCED = "SYNC", "✅ " + _("Synced")
        FAILED = "FAIL", "❌ " + _("Failed")

    class Action(models.TextChoices):
        # Database Value, Human Readable Label
        WRITE = "WRT", "🔗 " + _("Write")
        DELETE = "DEL", "🔪 " + _("Delete")

    action = models.CharField(
        max_length=max(len(c[0]) for c in Action.choices), choices=Action.choices
    )
    user_id = models.CharField(max_length=255)
    relation = models.CharField(max_length=100)
    object_id = models.CharField(max_length=255)

    status = models.CharField(
        max_length=max(len(c[0]) for c in Status.choices),
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    retry_count = models.IntegerField(default=0)
    # T2.8: when this row was last claimed (set together with the IN_FLIGHT
    # transition). The drain's reaper uses this to reclaim rows whose claiming
    # worker died mid-call, so a dead worker can never stall a row forever.
    claimed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ReBac Sync Task"
        verbose_name_plural = "ReBac Sync Tasks"
        ordering = ("created_at", "id")

    def __str__(self) -> str:
        return f"{self.action} {self.relation} for {self.object_id} ({self.status})"
