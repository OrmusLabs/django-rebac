# tests/test_views_and_mixins.py
import logging
from typing import ClassVar

import pytest
from django.core.exceptions import ImproperlyConfigured
from openfga_sdk.exceptions import ValidationException
from rest_framework import generics
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from rebac.models import RebacSyncOutbox
from rebac.permissions import IsRebacAuthorized
from rebac.structs import RebacViewConfig
from rebac.views import RebacViewMixin
from tests.models import MockFolder

pytestmark = pytest.mark.django_db


# ==========================================
# TEST FIXTURE VIEWS
# ==========================================
class DummyListAPIView(RebacViewMixin, generics.ListAPIView):
    """A concrete implementation of the RebacViewMixin and ListAPIView for testing."""

    queryset = MockFolder.objects.all()
    rebac_config = RebacViewConfig(
        object_type="folder",
        read_relation="can_list",
    )


class DummyRebacViewMixin(RebacViewMixin, generics.RetrieveUpdateDestroyAPIView):
    """A concrete implementation of the RebacViewMixin for testing."""

    queryset = MockFolder.objects.all()
    lookup_field = "pk"

    rebac_config = RebacViewConfig(
        object_type="folder",
        read_relation="can_read",
        update_relation="can_update",
        delete_relation="can_delete",
        create_scope_type="organization",
        create_scope_field="org_id",
        create_relation="can_add_folder",
    )


# ==========================================
# THE TEST SUITE
# ==========================================
class TestViewsAndMixins:
    # tests/test_views_and_mixins.py

    def test_authorized_list_api_view_filters_queryset(self, api_rf, mock_rebac_client):
        """Verifies the standard ListAPIView correctly asks ReBAC and filters the DB."""
        folder1 = MockFolder.objects.create(name="Public", org_id="o1", creator_id="u1")
        folder2 = MockFolder.objects.create(name="Private", org_id="o2", creator_id="u2")

        request = api_rf.get("/dummy/")
        request.rebac_user = "user:bob"

        view = DummyListAPIView()
        view.request = request
        view.kwargs = {}

        # Return a raw Python list of strings (No '.objects' and no 'type:' prefix)
        mock_rebac_client.list_objects.return_value = [str(folder1.id)]

        qs = view.get_queryset()

        assert qs.count() == 1
        assert qs.first().id == folder1.id

    def test_RebacViewMixin_list_filtering(self, api_rf, mock_rebac_client):
        """Verifies the RebacViewMixin Hook 1 (List Filtering)."""
        folder1 = MockFolder.objects.create(name="F1", org_id="o1", creator_id="u1")

        request = api_rf.get("/dummy/")
        request.rebac_user = "user:bob"

        view = DummyRebacViewMixin()
        view.request = request
        view.kwargs = {}

        # Return a raw list
        mock_rebac_client.list_objects.return_value = [str(folder1.id)]
        qs = view.get_queryset()

        assert qs.count() == 1

    def test_RebacViewMixin_disable_list_filtering(self, api_rf, mock_rebac_client):
        """Verifies the explicit bypass flag ignores FGA list filtering entirely."""
        MockFolder.objects.create(name="F1", org_id="o1", creator_id="u1")

        request = api_rf.get("/dummy/")
        request.rebac_user = "user:bob"

        view = DummyRebacViewMixin()
        view.request = request
        view.kwargs = {}

        view.rebac_config = RebacViewConfig(
            object_type="folder",
            read_relation="can_read",
            disable_list_filter=True,  # Bypass ReBAC!
        )

        qs = view.get_queryset()
        assert qs.count() == 1
        mock_rebac_client.list_objects.assert_not_called()

    def test_RebacViewMixin_create_parent_check_allowed(self, api_rf, mock_rebac_client):
        """Verifies the RebacViewMixin Hook 2 (POST Parent Checking)."""
        wsgi_request = api_rf.post("/dummy/", {"org_id": "org_999"}, format="json")

        view = DummyRebacViewMixin()

        # Use the view's native initializer to attach the JSON parsers!
        drf_request = view.initialize_request(wsgi_request)
        drf_request.rebac_user = "user:bob"
        view.request = drf_request

        # Setup FGA to ALLOW the creation
        mock_rebac_client.check.return_value.allowed = True

        # If it passes, it shouldn't raise any exceptions
        view.check_permissions(drf_request)

    def test_RebacViewMixin_create_parent_check_denied(self, api_rf, mock_rebac_client):
        """Verifies the RebacViewMixin blocks creation if the user lacks parent roles."""
        wsgi_request = api_rf.post("/dummy/", {"org_id": "org_999"}, format="json")

        view = DummyRebacViewMixin()
        drf_request = view.initialize_request(wsgi_request)
        drf_request.rebac_user = "user:mallory"
        view.request = drf_request

        mock_rebac_client.check.return_value = False

        with pytest.raises(PermissionDenied):
            view.check_permissions(drf_request)

    def test_RebacViewMixin_detail_object_check(self, api_rf, mock_rebac_client):
        """Verifies the RebacViewMixin Hook 3 (Detail Object Checking)."""
        folder1 = MockFolder.objects.create(name="F1", org_id="o1", creator_id="u1")
        request = api_rf.put(f"/dummy/{folder1.id}/", {})
        request.rebac_user = "user:bob"

        view = DummyRebacViewMixin()
        view.request = request
        view.kwargs = {"pk": folder1.id}

        mock_rebac_client.check.return_value = True

        view.check_object_permissions(request, folder1)

        mock_rebac_client.check.assert_called_once()

        called_kwargs = mock_rebac_client.check.call_args.kwargs
        assert called_kwargs["relation"] == "can_update"

    def test_RebacViewMixin_missing_identity_context(self, api_rf):
        """Verifies 401 is raised if the identity middleware failed to set the user."""
        request = api_rf.get("/dummy/")
        view = DummyRebacViewMixin()
        view.request = request

        with pytest.raises(AuthenticationFailed, match="Missing identity context"):
            view._get_rebac_user()

    def test_RebacViewMixin_injects_context_to_serializer(self, api_rf, mocker):
        """Verifies the mixin injects ReBAC variables to the serializer context."""
        request = api_rf.get("/dummy/")
        request.rebac_user = "user:bob"

        view = DummyRebacViewMixin()
        view.request = request
        view.format_kwarg = None

        mocker.patch("rebac.views.mixins.get_rebac_client", return_value="dummy_client")

        context = view.get_serializer_context()

        assert context["rebac_user"] == "user:bob"
        assert context["rebac_client"] == "dummy_client"

    def test_RebacViewMixin_missing_rebac_user(self, api_rf):
        """Verifies the mixin crashes safely if Traefik user is missing."""
        view = DummyRebacViewMixin()
        view.request = api_rf.get("/dummy/")
        # Intentionally NOT setting view.request.rebac_user

        with pytest.raises(AuthenticationFailed) as exc:
            view._get_rebac_user()
        assert "Missing identity context" in str(exc.value)

    def test_RebacViewMixin_get_queryset_detail_route(self, api_rf):
        """Verifies get_queryset bypasses FGA checks if it's a detail route (has pk)."""
        view = DummyRebacViewMixin()
        view.request = api_rf.get("/dummy/1/")
        view.request.rebac_user = "user:bob"

        # DRF injects kwargs for detail routes (e.g., /dummy/{pk}/)
        view.kwargs = {"pk": "1"}
        qs = view.get_queryset()

        # Compare the compiled SQL strings, because DRF clones the queryset in memory
        assert str(qs.query) == str(view.queryset.all().query)

    def test_RebacViewMixin_get_queryset_missing_config(self, api_rf):
        """Verifies get_queryset bypasses FGA safely if list_relation is missing."""
        view = DummyRebacViewMixin()
        view.request = api_rf.get("/dummy/")
        view.request.rebac_user = "user:bob"
        view.kwargs = {}

        # THE FIX: Sabotage the config properly using the dataclass
        view.rebac_config = RebacViewConfig(object_type="folder", read_relation=None)

        qs = view.get_queryset()

        assert str(qs.query) == str(view.queryset.all().query)

    def test_RebacViewMixin_check_permissions_missing_payload_field(self, api_rf):
        """Verifies parent checking blocks creation if the payload field is missing."""
        wsgi_request = api_rf.post("/dummy/", {"wrong_key": "123"}, format="json")

        view = DummyRebacViewMixin()
        drf_request = view.initialize_request(wsgi_request)
        drf_request.rebac_user = "user:bob"
        view.request = drf_request

        with pytest.raises(PermissionDenied) as exc:
            view.check_permissions(drf_request)
        assert "Payload must include parent field" in str(exc.value)

    def test_RebacViewMixin_check_object_permissions_unmapped_method(self, api_rf):
        """Verifies object checks pass through safely if the HTTP method isn't mapped."""
        folder = MockFolder.objects.create(name="F1", org_id="o1", creator_id="u1")

        # Simulate a PATCH request, but we only configured PUT/DELETE/GET in DummyRebacViewMixin
        wsgi_request = api_rf.patch(f"/dummy/{folder.id}/", {"name": "test"}, format="json")

        view = DummyRebacViewMixin()
        drf_request = view.initialize_request(wsgi_request)
        drf_request.rebac_user = "user:bob"
        view.request = drf_request

        view.check_object_permissions(drf_request, folder)

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

    def test_instance_delete_queues_tuples(self):
        """Verifies the overridden delete() method queues DELETE actions."""
        folder = MockFolder.objects.create(name="Docs", org_id="org1", creator_id="user1")
        RebacSyncOutbox.objects.all().delete()

        # Trigger the instance-level delete method
        folder.delete()

        # It should queue 2 DELETE tasks (1 for the parent, 1 for the creator)
        assert RebacSyncOutbox.objects.filter(action=RebacSyncOutbox.Action.DELETE).count() == 2

    def test_RebacViewMixin_list_relation_override(self, api_rf, mock_rebac_client):
        """Verifies list_relation takes precedence over read_relation when filtering lists."""
        view = DummyRebacViewMixin()
        view.request = api_rf.get("/dummy/")
        view.request.rebac_user = "user:bob"
        view.kwargs = {}

        view.rebac_config = RebacViewConfig(
            object_type="folder",
            read_relation="can_read_detail",
            list_relation="can_list_specifically",
        )

        # Raw list
        mock_rebac_client.list_objects.return_value = []
        view.get_queryset()

        mock_rebac_client.list_objects.assert_called_once()

        called_kwargs = mock_rebac_client.list_objects.call_args.kwargs
        assert called_kwargs["relation"] == "can_list_specifically"

    def test_RebacViewMixin_disable_list_filter(self, api_rf, mock_rebac_client):
        """Verifies that disable_list_filter=True entirely bypasses OpenFGA network checks."""
        view = DummyRebacViewMixin()
        view.request = api_rf.get("/dummy/")
        view.request.rebac_user = "user:bob"
        view.kwargs = {}

        # 🤠 Override the config to explicitly opt-out of list filtering
        view.rebac_config = RebacViewConfig(
            object_type="folder",
            read_relation="can_read_detail",
            disable_list_filter=True,
        )

        qs = view.get_queryset()

        # Mathematical Proof: The network check MUST NOT be called
        mock_rebac_client.list_objects.assert_not_called()

        # Mathematical Proof: The queryset MUST NOT be filtered
        assert str(qs.query) == str(view.queryset.all().query)

    def test_RebacViewMixin_dsl_mismatch_raises_improperly_configured(
        self,
        api_rf,
        mock_rebac_client,
    ):
        """Verifies that an SDK error triggers our DX guardrail."""
        view = DummyRebacViewMixin()
        view.request = api_rf.get("/dummy/")
        view.request.rebac_user = "user:bob"
        view.kwargs = {}

        mock_rebac_client.list_objects.side_effect = Exception("Network Down")

        # Match the new generic error prefix from your mixin
        with pytest.raises(ImproperlyConfigured, match="ReBAC ListObjects validation failed"):
            view.get_queryset()

    def test_RebacViewMixin_parent_check_validation_error(self, api_rf, mock_rebac_client):
        """Verifies that a network error on POST parent check raises ImproperlyConfigured."""
        wsgi_request = api_rf.post("/dummy/", {"org_id": "org_999"}, format="json")
        view = DummyRebacViewMixin()
        drf_request = view.initialize_request(wsgi_request)
        drf_request.rebac_user = "user:bob"
        view.request = drf_request

        mock_rebac_client.check.side_effect = Exception("Network Down")

        # Match the new generic error prefix
        with pytest.raises(ImproperlyConfigured, match="ReBAC configuration or execution error"):
            view.check_permissions(drf_request)

    def test_RebacViewMixin_object_check_validation_error(self, api_rf, mock_rebac_client):
        """Verifies that a network error on an object check raises ImproperlyConfigured."""
        folder = MockFolder.objects.create(name="F1", org_id="o1", creator_id="u1")
        request = api_rf.put(f"/dummy/{folder.id}/", {})
        request.rebac_user = "user:bob"

        view = DummyRebacViewMixin()
        view.request = request
        view.kwargs = {"pk": folder.id}

        mock_rebac_client.check.side_effect = Exception("Network Down")

        # Match the new generic error prefix
        with pytest.raises(ImproperlyConfigured, match="ReBAC configuration or execution error"):
            view.check_object_permissions(request, folder)

    def test_RebacViewMixin_post_creation_model_property_fallback(self, api_rf, mock_rebac_client):
        """Verifies the Mixin falls back to instantiating the model to read a property."""

        class MockCompanyModel:
            @property
            def platform_id(self):
                return "model_provided_global_id"

        class MockQuerySet:
            model = MockCompanyModel

        class PropertyFallbackMixinView(RebacViewMixin, generics.GenericAPIView):
            queryset = MockQuerySet()

        wsgi_request = api_rf.post("/dummy/", {}, format="json")

        view = PropertyFallbackMixinView()
        view.rebac_config = RebacViewConfig(
            object_type="company",
            create_scope_type="platform",
            create_scope_field="platform_id",
            create_relation="can_create",
        )

        drf_request = view.initialize_request(wsgi_request)
        drf_request.rebac_user = "user:bob"
        view.request = drf_request

        mock_rebac_client.check.return_value = True

        view.check_permissions(drf_request)

        called_kwargs = mock_rebac_client.check.call_args.kwargs
        assert called_kwargs["obj"] == "platform:model_provided_global_id"

    def test_RebacViewMixin_duplicate_auth_warning(self, api_rf, caplog):
        """Verifies a warning is emitted if IsRebacAuthorized is used alongside RebacViewMixin."""

        class BadView(RebacViewMixin, generics.GenericAPIView):
            permission_classes: ClassVar[list] = [IsRebacAuthorized]
            rebac_config = RebacViewConfig(object_type="folder")

        with caplog.at_level(logging.WARNING):
            BadView()

        # Asserts updated 'ReBAC' log prefix instead of 'FGA'
        assert "Duplicate ReBAC Authorization detected" in caplog.text

    def test_RebacViewMixin_missing_view_config(self, api_rf):
        """Verifies missing config crashes gracefully."""
        from rest_framework import generics

        class BadView(RebacViewMixin, generics.GenericAPIView):
            rebac_config = None  # Missing config!

        view = BadView()
        with pytest.raises(
            ImproperlyConfigured, match="must define `rebac_config` of type `RebacViewConfig`"
        ):
            view._get_config()

    def test_RebacViewMixin_delete_method_maps_correctly(self, api_rf, mock_rebac_client):
        """Verifies DELETE requests correctly use delete_relation."""
        folder = MockFolder.objects.create(name="F1", org_id="o1", creator_id="u1")
        request = api_rf.delete(f"/dummy/{folder.id}/")
        request.rebac_user = "user:bob"

        view = DummyRebacViewMixin()
        view.request = request
        view.kwargs = {"pk": folder.id}

        mock_rebac_client.check.return_value = True
        view.check_object_permissions(request, folder)

        mock_rebac_client.check.assert_called_once()

        called_kwargs = mock_rebac_client.check.call_args.kwargs
        assert called_kwargs["relation"] == "can_delete"

    def test_RebacViewMixin_get_method_maps_correctly(self, api_rf, mock_rebac_client):
        """Verifies GET requests correctly use read_relation."""
        folder = MockFolder.objects.create(name="F1", org_id="o1", creator_id="u1")
        request = api_rf.get(f"/dummy/{folder.id}/")
        request.rebac_user = "user:bob"

        view = DummyRebacViewMixin()
        view.request = request
        view.kwargs = {"pk": folder.id}

        mock_rebac_client.check.return_value = True
        view.check_object_permissions(request, folder)

        mock_rebac_client.check.assert_called_once()

        called_kwargs = mock_rebac_client.check.call_args.kwargs
        assert called_kwargs["relation"] == "can_read"

    def test_RebacViewMixin_lookup_header_routes_to_object_permissions(
        self, api_rf, mock_rebac_client
    ):
        """Verifies stateless routing works perfectly for mixins."""
        request = api_rf.get("/dummy/", HTTP_X_CONTEXT_ORG_ID="acme_123")
        request.rebac_user = "user:bob"

        view = DummyRebacViewMixin()
        view.request = request
        view.kwargs = {}

        view.rebac_config = RebacViewConfig(
            object_type="organization",
            read_relation="can_read",
            lookup_header="HTTP_X_CONTEXT_ORG_ID",
        )

        mock_rebac_client.check.return_value = True

        view.check_permissions(request)

        mock_rebac_client.check.assert_called_once()

        called_kwargs = mock_rebac_client.check.call_args.kwargs
        assert called_kwargs["obj"] == "organization:acme_123"
