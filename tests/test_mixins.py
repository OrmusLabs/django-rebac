# tests/test_mixins.py
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models

from rebac.core.adapters import RebacTupleAdapter
from rebac.models import RebacModelSyncMixin, RebacSyncOutbox

from .models import MockCascadeChild, MockCascadeParent, MockFolder, MockOrganization

# Ensure all tests in this file have database access and are rolled back afterward
pytestmark = pytest.mark.django_db


class TestRebacModelSyncMixin:
    def test_tuple_generation_on_create(self):
        """Verifies that creating a new object queues the correct WRITE tuples."""
        folder = MockFolder.objects.create(
            name="Top Secret", org_id="org_123", creator_id="user_999"
        )

        # Assert 2 tasks were queued: 1 for the parent, 1 for the creator
        pending_tasks = RebacSyncOutbox.objects.filter(status=RebacSyncOutbox.Status.PENDING)
        assert pending_tasks.count() == 2

        # Verify the parent tuple
        parent_task = pending_tasks.get(relation="organization")
        assert parent_task.action == RebacSyncOutbox.Action.WRITE
        assert parent_task.user_id == "organization:org_123"
        assert parent_task.object_id == f"folder:{folder.pk}"

        # Verify the creator tuple
        creator_task = pending_tasks.get(relation="owner")
        assert creator_task.action == RebacSyncOutbox.Action.WRITE
        assert creator_task.user_id == "user:user_999"

    def test_tuple_diffing_on_update(self):
        """Verifies that updating a relationship deletes the old tuple and writes the new one."""
        folder = MockFolder.objects.create(name="Docs", org_id="old_org", creator_id="user_1")

        # Clear the outbox to isolate the update logic
        RebacSyncOutbox.objects.all().delete()

        # Mutate the parent organization and save
        folder.org_id = "new_org"
        folder.save()

        # The mixin should have calculated the diff: DELETE old_org, WRITE new_org
        tasks = RebacSyncOutbox.objects.all().order_by("created_at")
        assert tasks.count() == 2

        delete_task = tasks.get(action=RebacSyncOutbox.Action.DELETE)
        assert delete_task.user_id == "organization:old_org"

        write_task = tasks.get(action=RebacSyncOutbox.Action.WRITE)
        assert write_task.user_id == "organization:new_org"

    def test_tuple_generation_on_delete(self):
        """Verifies that deleting a model queues DELETE actions for all its tuples."""
        org = MockOrganization.objects.create(name="Acme", creator_id="admin_1")

        RebacSyncOutbox.objects.all().delete()
        org_id = org.pk
        org.delete()

        # Assert the cleanup tuple was queued
        task = RebacSyncOutbox.objects.get()
        assert task.action == RebacSyncOutbox.Action.DELETE
        assert task.user_id == "user:admin_1"
        assert task.object_id == f"organization:{org_id}"

    def test_fga_sync_mixin_missing_config(self):
        """Verifies an ImproperlyConfigured error is raised if rebac_config is invalid."""

        folder = MockFolder(name="Test", org_id="org1", creator_id="user1")

        # Temporarily sabotage the strict configuration
        folder.rebac_config = None

        with pytest.raises(ImproperlyConfigured) as exc:
            # Call the adapter directly, just as the mixin would
            RebacTupleAdapter.generate_tuples(folder, folder.rebac_config)

        assert "provided an invalid `rebac_config`" in str(exc.value)

    def test_tuple_diffing_no_changes(self):
        """Verifies that saving an object without changing FGA relations queues nothing."""
        folder = MockFolder.objects.create(name="Docs", org_id="org1", creator_id="user1")

        # Clear the outbox from the initial creation
        RebacSyncOutbox.objects.all().delete()

        # Mutate a field that has NO impact on OpenFGA relationships
        folder.name = "Updated Docs Name"
        folder.save()

        # The tuple sets should match exactly, meaning no diffs were queued
        assert RebacSyncOutbox.objects.count() == 0

    def test_instance_delete_queues_tuples(self):
        """Verifies the overridden delete() method queues DELETE actions."""
        folder = MockFolder.objects.create(name="Docs", org_id="org1", creator_id="user1")
        RebacSyncOutbox.objects.all().delete()

        # Trigger the instance-level delete method
        folder.delete()

        # It should queue 2 DELETE tasks (1 for the parent, 1 for the creator)
        assert RebacSyncOutbox.objects.filter(action=RebacSyncOutbox.Action.DELETE).count() == 2

    def test_RebacModelSyncMixin_init_missing_config(self):
        """Verifies __init__ raises ImproperlyConfigured if rebac_config is missing."""

        class BadModel(RebacModelSyncMixin, models.Model):
            class Meta:
                app_label = "rebac"

        with pytest.raises(ImproperlyConfigured, match="must define a 'rebac_config' attribute"):
            BadModel()

    def test_RebacModelSyncMixin_save_delete_missing_config(self):
        """Verifies save and delete raise ImproperlyConfigured if mutated."""
        folder = MockFolder(name="Test", org_id="org1", creator_id="user1")

        # Sabotage the config after instantiation
        folder.rebac_config = None

        with pytest.raises(ImproperlyConfigured, match="must define a 'rebac_config' attribute"):
            folder.save()

        with pytest.raises(ImproperlyConfigured, match="must define a 'rebac_config' attribute"):
            folder.delete()


class TestRebacDeletionMechanisms:
    """
    Rigorously verifies the three vectors of Django object deletion to ensure
    the ReBAC graph remains perfectly synchronized without duplicating Outbox tasks.
    """

    def test_instance_delete_prevents_duplicate_signals(self) -> None:
        """
        Condition 1: Instance `.delete()`

        Verifies that manually calling `folder.delete()` triggers the mixin's
        instance method, sets the sentinel flag, and PREVENTS the `pre_delete`
        signal from duplicating the outbox tasks.
        """
        # 1. Setup
        folder = MockFolder.objects.create(name="Docs", org_id="org1", creator_id="user1")
        RebacSyncOutbox.objects.all().delete()  # Clear the Outbox of creation tasks

        # 2. Action (Fires both the .delete() method AND the pre_delete signal)
        folder.delete()

        # 3. Assert
        # MockFolder has 1 parent (organization) and 1 creator (owner).
        # Therefore, exactly 2 DELETE tasks should exist.
        # If the signal wasn't blocked by our sentinel flag, there would be 4!
        tasks = RebacSyncOutbox.objects.filter(action=RebacSyncOutbox.Action.DELETE)
        assert tasks.count() == 2, "Duplicate tasks were queued! Sentinel flag failed."

    def test_bulk_queryset_delete_caught_by_signal(self) -> None:
        """
        Condition 2 & 3: Bulk Deletes & Cascades

        Verifies that when Django's internal Collector bypasses the instance
        `.delete()` method (which happens natively in both `QuerySet.delete()`
        and ForeignKey `CASCADE` events), the metaprogrammed `pre_delete` signal
        successfully traps the event and queues the ReBAC tuples.
        """
        # 1. Setup: Create multiple folders
        MockFolder.objects.create(name="F1", org_id="org1", creator_id="user_A")
        MockFolder.objects.create(name="F2", org_id="org1", creator_id="user_B")
        RebacSyncOutbox.objects.all().delete()  # Clear the Outbox of creation tasks

        # 2. Action: Bulk Delete
        # This translates directly to SQL bulk deletion and bypasses the model methods
        MockFolder.objects.filter(org_id="org1").delete()

        # 3. Assert
        # 2 folders * 2 relations each (parent + creator) = 4 DELETE tasks
        tasks = RebacSyncOutbox.objects.filter(action=RebacSyncOutbox.Action.DELETE)
        assert tasks.count() == 4, "Signal failed to catch the bulk deletion!"

        # 4. Deep Validation: Ensure the correct ReBAC tuples were generated
        users_in_tasks = set(tasks.values_list("user_id", flat=True))
        assert "user:user_A" in users_in_tasks
        assert "user:user_B" in users_in_tasks
        assert "organization:org1" in users_in_tasks

    def test_duplicate_signal_execution_safety(self) -> None:
        """
        Condition: Signal Idempotency

        Simulates what happens if Django accidentally fires the pre_delete signal
        twice for the exact same object in memory (e.g., due to a misconfigured
        third-party app or a forced manual signal broadcast).
        """
        # 1. Setup
        folder = MockFolder.objects.create(name="Docs", org_id="org1", creator_id="user1")
        RebacSyncOutbox.objects.all().delete()

        # 2. Action: Manually fire the framework's signal handler twice
        # to simulate a rogue duplicate broadcast
        MockFolder._rebac_auto_cascade_handler(sender=MockFolder, instance=folder)
        MockFolder._rebac_auto_cascade_handler(sender=MockFolder, instance=folder)

        # 3. Assert
        # Even though the handler was hit twice, the `_rebac_is_deleting` flag
        # ensures the logic only executes once.
        tasks = RebacSyncOutbox.objects.filter(action=RebacSyncOutbox.Action.DELETE)
        assert tasks.count() == 2, "Signal handler is not idempotent!"

    def test_true_foreign_key_cascade_caught_by_signal(self) -> None:
        """
        Condition 3: True Django ORM Foreign Key CASCADE
        """

        # 1. Setup: Create a strict hierarchy (Added creator_id)
        parent = MockCascadeParent.objects.create(name="HQ", creator_id="admin_user")
        child_1 = MockCascadeChild.objects.create(parent=parent)
        child_2 = MockCascadeChild.objects.create(parent=parent)

        # ⚠️ THE FIX: Capture the primary keys BEFORE they are wiped by Django
        parent_pk = parent.pk
        child_1_pk = child_1.pk
        child_2_pk = child_2.pk

        # Clear the outbox of the creation tasks
        RebacSyncOutbox.objects.all().delete()

        # 2. Action: Delete the PARENT
        parent.delete()

        # 3. Assert: Verify the Outbox caught all 3 deletions
        tasks = RebacSyncOutbox.objects.filter(action=RebacSyncOutbox.Action.DELETE)
        assert tasks.count() == 3, "Signal failed to catch the cascading children!"

        # 4. Deep Validation: Ensure exact objects were queued using the CAPTURED keys
        deleted_objects = set(tasks.values_list("object_id", flat=True))
        assert f"parent:{parent_pk}" in deleted_objects
        assert f"child:{child_1_pk}" in deleted_objects
        assert f"child:{child_2_pk}" in deleted_objects
