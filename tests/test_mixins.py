# tests/test_mixins.py
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models

from rebac.adapters import RebacTupleAdapter
from rebac.models import RebacModelSyncMixin, RebacSyncOutbox

from .models import MockFolder, MockOrganization

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
