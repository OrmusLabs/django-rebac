# tests/test_serializers.py
import pytest
from rest_framework import serializers

from rebac.backends.base.exceptions import RebacConnectionError
from rebac.serializers import RebacPermissionSerializerMixin
from tests.models import MockFolder

pytestmark = pytest.mark.django_db


# ==========================================
# 🛠️ TEST FIXTURE SERIALIZER
# ==========================================
class DummyFolderSerializer(RebacPermissionSerializerMixin, serializers.ModelSerializer):
    """
    A concrete implementation of the mixin to test FGA integration.
    Notice we deliberately LEAVE OUT '_permissions' from the fields list
    to prove the auto-injection works.
    """

    class Meta:
        model = MockFolder
        fields = ("id", "name")  # No _permissions listed!

        rebac_object_type = "folder"
        rebac_permissions = ("can_read", "can_edit")


# ==========================================
# 🧪 SERIALIZER TEST SUITE
# ==========================================
class TestRebacSerializers:
    def test_auto_injection_of_permissions_field(self):
        """Verifies the mixin automatically forces '_permissions' into the serializer fields."""
        serializer = DummyFolderSerializer()

        # Mathematical Proof: It was not in the Meta, but it IS in the final fields
        assert "_permissions" in serializer.fields

    def test_detail_view_single_batch_evaluation(self, api_rf, mock_rebac_client):
        """Verifies a single object triggers a mini-batch check and maps correctly."""
        folder = MockFolder.objects.create(name="Top Secret", org_id="o1", creator_id="u1")

        request = api_rf.get("/dummy/1/")
        request.rebac_user = "user:bob"

        # Return the simple dictionary our abstract interface dictates
        mock_rebac_client.batch_check.return_value = {
            f"folder:{folder.id}": {"can_read": True, "can_edit": False}
        }

        serializer = DummyFolderSerializer(folder, context={"request": request})
        data = serializer.data

        # 1. Verify standard fields
        assert data["name"] == "Top Secret"

        # 2. Verify FGA Permissions map
        assert data["_permissions"]["can_read"] is True
        assert data["_permissions"]["can_edit"] is False

        # 3. Verify it used the client exactly once
        mock_rebac_client.batch_check.assert_called_once()

    def test_list_view_batch_evaluation_prevents_n_plus_one(self, api_rf, mock_rebac_client):
        """
        Verifies `many=True` triggers the Custom List Serializer,
        evaluating ALL items in a SINGLE network request.
        """
        f1 = MockFolder.objects.create(name="Public", org_id="o1", creator_id="u1")
        f2 = MockFolder.objects.create(name="Private", org_id="o1", creator_id="u1")

        request = api_rf.get("/dummy/")
        request.rebac_user = "user:bob"

        # Simulate FGA Network Response for Multiple Objects!
        mock_rebac_client.batch_check.return_value = {
            f"folder:{f1.id}": {"can_read": True, "can_edit": True},
            f"folder:{f2.id}": {"can_read": True, "can_edit": False},
        }

        # Execute with many=True
        serializer = DummyFolderSerializer([f1, f2], many=True, context={"request": request})
        data = serializer.data

        # 1. Mathematical Proof of N+1 Prevention:
        # We checked 2 items (with 2 permissions each), but the network fired exactly ONCE.
        mock_rebac_client.batch_check.assert_called_once()

        # 2. Verify Data mapped correctly to the exact list items
        assert data[0]["id"] == f1.id
        assert data[0]["_permissions"]["can_edit"] is True

        assert data[1]["id"] == f2.id
        assert data[1]["_permissions"]["can_edit"] is False

    def test_missing_rebac_user_fails_gracefully(self, api_rf, mock_rebac_client):
        """Verifies if the Traefik identity is missing, it safely returns False for all perms."""
        folder = MockFolder.objects.create(name="Doc", org_id="o1", creator_id="u1")
        request = api_rf.get("/dummy/1/")

        # Deliberately NOT setting request.rebac_user

        serializer = DummyFolderSerializer(folder, context={"request": request})
        data = serializer.data

        # Should default to False and bypass network
        assert data["_permissions"]["can_read"] is False
        mock_rebac_client.batch_check.assert_not_called()

    def test_network_failure_fails_gracefully(self, api_rf, mock_rebac_client):
        """Verifies network timeouts do not crash the view, returning False for perms."""
        folder = MockFolder.objects.create(name="Doc", org_id="o1", creator_id="u1")
        request = api_rf.get("/dummy/1/")
        request.rebac_user = "user:bob"

        # Force an SDK failure
        mock_rebac_client.batch_check.side_effect = RebacConnectionError("FGA is down")

        serializer = DummyFolderSerializer(folder, context={"request": request})
        data = serializer.data

        # The view should still render the JSON, just with buttons disabled
        assert data["_permissions"]["can_read"] is False
        assert data["name"] == "Doc"

    def test_list_view_network_failure_fails_gracefully(self, api_rf, mock_rebac_client):
        """Verifies list view network timeouts do not crash the batcher."""
        f1 = MockFolder.objects.create(name="Public", org_id="o1", creator_id="u1")
        f2 = MockFolder.objects.create(name="Private", org_id="o1", creator_id="u1")

        request = api_rf.get("/dummy/")
        request.rebac_user = "user:bob"

        mock_rebac_client.batch_check.side_effect = RebacConnectionError("FGA is down")

        serializer = DummyFolderSerializer([f1, f2], many=True, context={"request": request})
        data = serializer.data

        assert data[0]["_permissions"]["can_read"] is False
        assert data[1]["_permissions"]["can_read"] is False

    def test_list_serialization_evaluates_queryset_once(self, api_rf, mock_rebac_client):
        """The batcher must materialize the queryset exactly once (no double fetch)."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        folder = MockFolder.objects.create(name="Doc", org_id="o1", creator_id="u1")
        request = api_rf.get("/dummy/")
        request.rebac_user = "user:bob"
        mock_rebac_client.batch_check.return_value = {
            f"folder:{folder.id}": {"can_read": True, "can_edit": False}
        }

        serializer = DummyFolderSerializer(
            MockFolder.objects.all(), many=True, context={"request": request}
        )

        with CaptureQueriesContext(connection) as ctx:
            data = serializer.data

        selects = [q for q in ctx.captured_queries if q["sql"].strip().upper().startswith("SELECT")]
        assert data[0]["_permissions"]["can_read"] is True
        # One SELECT for the row data — not two (cloned `.all()` + original iteration).
        assert len(selects) == 1

    def test_shared_context_maps_are_namespaced_per_object_type(self, api_rf, mock_rebac_client):
        """
        DRF shares one context dict across the whole serializer tree. Two lists of
        different object types must write to different keys, so serializing one can
        never clobber the other's permissions map (context bleed).
        """
        from tests.models import MockOrganization

        class DummyOrgSerializer(RebacPermissionSerializerMixin, serializers.ModelSerializer):
            class Meta:
                model = MockOrganization
                fields = ("id", "name")

                rebac_object_type = "organization"
                rebac_permissions = ("can_view",)

        # First rows of both tables share pk=1 — the exact collision condition.
        folder = MockFolder.objects.create(name="F", org_id="o1", creator_id="u1")
        org = MockOrganization.objects.create(name="O", creator_id="admin")
        assert folder.pk == org.pk == 1

        request = api_rf.get("/dummy/")
        request.rebac_user = "user:bob"

        context = {"request": request}
        mock_rebac_client.batch_check.side_effect = [
            {"folder:1": {"can_read": True, "can_edit": False}},
            {"organization:1": {"can_view": True}},
        ]

        # 1. Serialize the folder list into the shared context.
        folder_list = DummyFolderSerializer([folder], many=True, context=context)
        folder_data = folder_list.data
        assert folder_data[0]["_permissions"]["can_read"] is True

        # 2. Serialize the organization list into the SAME context.
        org_data = DummyOrgSerializer([org], many=True, context=context).data
        assert org_data[0]["_permissions"]["can_view"] is True

        # 3. The folder map must still be intact for late reads (e.g. a parent
        #    list whose `_permissions` render after its nested list).
        late = folder_list.child.get__permissions(folder)
        assert late["can_read"] is True

        # No unnamespaced shared key may exist; both maps coexist.
        assert "rebac_permissions_map" not in context
        assert context["rebac_permissions_map::folder"] == {
            "folder:1": {"can_read": True, "can_edit": False}
        }
        assert context["rebac_permissions_map::organization"] == {
            "organization:1": {"can_view": True}
        }
