# tests/models.py
from django.db import models

from rebac.core.structs import RebacCreatorConfig, RebacModelConfig, RebacParentConfig
from rebac.models.mixins import RebacModelSyncMixin


class MockOrganization(RebacModelSyncMixin, models.Model):
    name = models.CharField(max_length=50)
    creator_id = models.CharField(max_length=50)

    rebac_config = RebacModelConfig(
        object_type="organization",
        creators=[RebacCreatorConfig(relation="admin", local_field="creator_id")],
    )

    class Meta:
        # 🛠️ Explicitly attach this test model to our package's app registry
        app_label = "rebac"


class MockFolder(RebacModelSyncMixin, models.Model):
    name = models.CharField(max_length=50)
    org_id = models.CharField(max_length=50)
    creator_id = models.CharField(max_length=50)

    rebac_config = RebacModelConfig(
        object_type="folder",
        parents=[
            RebacParentConfig(
                relation="organization",
                parent_type="organization",
                local_field="org_id",
            )
        ],
        creators=[RebacCreatorConfig(relation="owner", local_field="creator_id")],
    )

    class Meta:
        # 🛠️ Explicitly attach this test model to our package's app registry
        app_label = "rebac"


# For Finance Tests


class Invoice(models.Model):
    """Mock Invoice model for testing the stateless dashboard."""

    organization_id = models.CharField(max_length=255, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=50, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # 🛠️ Explicitly attach this test model to our package's test app registry
        app_label = "rebac"


class Expense(models.Model):
    """Mock Expense model for testing the stateless dashboard."""

    organization_id = models.CharField(max_length=255, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # 🛠️ Explicitly attach this test model to our package's test app registry
        app_label = "rebac"


class MockCascadeParent(RebacModelSyncMixin, models.Model):
    """A strictly related parent model to test database cascades."""

    name = models.CharField(max_length=50)
    # ⚠️ Added: A physical field to track ownership
    creator_id = models.CharField(max_length=50)

    # ⚠️ Added: The configuration to map the creator to the graph
    rebac_config = RebacModelConfig(
        object_type="parent",
        creators=[RebacCreatorConfig(relation="owner", local_field="creator_id")],
    )

    class Meta:
        app_label = "rebac"


class MockCascadeChild(RebacModelSyncMixin, models.Model):
    """A strictly related child model to prove signals fire on CASCADE."""

    # ⚠️ THE CRITICAL DIFFERENCE: A true physical ForeignKey with CASCADE
    parent = models.ForeignKey(MockCascadeParent, on_delete=models.CASCADE)

    rebac_config = RebacModelConfig(
        object_type="child",
        parents=[
            RebacParentConfig(
                relation="parent",
                parent_type="parent",
                local_field="parent_id",  # Reads the underlying FK column
            )
        ],
    )

    class Meta:
        app_label = "rebac"
