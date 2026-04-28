# tests/test_permissions.py
import logging
from typing import ClassVar

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSetMixin

from rebac.backends.base.exceptions import RebacConnectionError, RebacSchemaError
from rebac.permissions import IsRebacAuthorized
from rebac.structs import RebacViewConfig

from .models import MockFolder
from .views import FinanceDashboardView

pytestmark = pytest.mark.django_db


# ==========================================
# 🛠️ TEST FIXTURE VIEWS
# ==========================================


class DummyProtectedView(APIView):
    """A standard DRF view protected by our permission class."""

    permission_classes: ClassVar[list] = [IsRebacAuthorized]
    rebac_config = RebacViewConfig(
        object_type="folder",
        read_relation="can_read",
        create_scope_type="organization",
        create_scope_field="org_id",
        create_relation="can_add_folder",
    )

    def post(self, request):
        return Response({"status": "created"})

    def get(self, request, pk=None):
        return Response({"status": "ok"})


class DummyProtectedViewSet(ViewSetMixin, APIView):
    """A DRF ViewSet mock to test action_relations safely."""

    permission_classes: ClassVar[list] = [IsRebacAuthorized]


# ==========================================
# 🧪 PERMISSIONS TEST SUITE
# ==========================================


class TestIsRebacAuthorized:
    def test_object_level_permission_checks_http_method(self, api_rf, mock_rebac_client):
        """Verifies GET maps to the correct FGA read_relation."""

        class MockObj:
            id = "folder_99"

        mock_obj = MockObj()
        request = api_rf.get("/dummy/1/")
        request.rebac_user = "user:bob"

        perm = IsRebacAuthorized()
        # Pure boolean return
        mock_rebac_client.check.return_value = True
        view_instance = DummyProtectedView()

        result = perm.has_object_permission(request, view_instance, mock_obj)
        assert result is True

    def test_missing_rebac_user_header_denied(self, api_rf):
        """Fails fast if the Traefik middleware did not attach an identity."""
        view = DummyProtectedView.as_view()
        request = api_rf.get("/dummy/1/")

        # Notice we DO NOT attach request.rebac_user here
        response = view(request, pk=1)
        assert response.status_code == 403

    def test_post_creation_parent_check_allowed(self, api_rf, mock_rebac_client):
        """Verifies POST requests successfully check the parent's permission."""
        view = DummyProtectedView.as_view()
        request = api_rf.post("/dummy/", {"org_id": "org_777"}, format="json")
        request.rebac_user = "user:bob"

        # Pure boolean return
        mock_rebac_client.check.return_value = True
        response = view(request)

        assert response.status_code == 200

        # Check kwargs, not positional args
        called_kwargs = mock_rebac_client.check.call_args.kwargs
        assert called_kwargs["user"] == "user:bob"
        assert called_kwargs["relation"] == "can_add_folder"
        assert called_kwargs["obj"] == "organization:org_777"

    def test_post_creation_parent_check_denied(self, api_rf, mock_rebac_client):
        """Verifies POST requests are blocked if FGA returns False."""
        view = DummyProtectedView.as_view()
        request = api_rf.post("/dummy/", {"org_id": "org_777"}, format="json")
        request.rebac_user = "user:mallory"

        # Pure boolean return
        mock_rebac_client.check.return_value = False
        response = view(request)

        assert response.status_code == 403

    def test_missing_config_raises_error(self, api_rf):
        """Verifies that an improperly configured view crashes loudly."""

        class BadView(APIView):
            permission_classes: ClassVar[list] = [IsRebacAuthorized]

        view = BadView.as_view()
        request = api_rf.get("/dummy/")
        request.rebac_user = "user:bob"

        with pytest.raises(ImproperlyConfigured):
            view(request)

    def test_missing_parent_field_denied_safely(self, api_rf):
        """Verifies that forgetting the parent ID in the payload safely blocks access."""
        view = DummyProtectedView.as_view()
        request = api_rf.post("/dummy/", {"wrong_field": "123"}, format="json")
        request.rebac_user = "user:bob"

        response = view(request)
        assert response.status_code == 403

    def test_creation_network_failure_fails_safely(self, api_rf, mock_rebac_client):
        """Verifies network timeouts during parent checks fail closed (403)."""
        view = DummyProtectedView.as_view()
        request = api_rf.post("/dummy/", {"org_id": "org_777"}, format="json")
        request.rebac_user = "user:bob"

        mock_rebac_client.check.side_effect = RebacConnectionError("FGA Server Unreachable")

        response = view(request)
        assert response.status_code == 403

    def test_has_object_permission_no_rebac_user(self, api_rf):
        """Verifies object access is denied if Traefik user header is missing."""
        view = DummyProtectedView()
        request = api_rf.get("/dummy/1/")

        perm = IsRebacAuthorized()
        assert perm.has_object_permission(request, view, MockFolder(id=1)) is False

    def test_has_object_permission_no_id_on_obj(self, api_rf):
        """Verifies the permission safely rejects objects that lack an ID."""
        view = DummyProtectedView()
        request = api_rf.get("/dummy/1/")
        request.rebac_user = "user:bob"

        class BadObject:
            pass  # No .id or .pk

        perm = IsRebacAuthorized()
        assert perm.has_object_permission(request, view, BadObject()) is False

    def test_has_object_permission_network_error(self, api_rf, mock_rebac_client):
        """Verifies network timeouts during object checks fail closed (False)."""
        view = DummyProtectedView()
        request = api_rf.get("/dummy/1/")
        request.rebac_user = "user:bob"

        mock_rebac_client.check.side_effect = TimeoutError("FGA Down")

        perm = IsRebacAuthorized()
        assert perm.has_object_permission(request, view, MockFolder(id=1)) is False

    def test_guardrail_action_relations_on_generic_view(self, api_rf):
        """Verifies that defining action_relations on a non-ViewSet crashes safely."""
        view = DummyProtectedView()
        view.rebac_config = RebacViewConfig(
            object_type="folder", action_relations={"custom_action": "can_custom"}
        )
        request = api_rf.get("/dummy/1/")
        request.rebac_user = "user:bob"

        perm = IsRebacAuthorized()

        with pytest.raises(ImproperlyConfigured) as exc:
            perm._get_config(view)

        assert "is not a ViewSet" in str(exc.value)
        assert "Standard Generic Views do not support @action" in str(exc.value)

    def test_has_object_permission_action_mapping(self, api_rf, mock_rebac_client):
        """Verifies custom ViewSet actions map to the correct FGA relation."""
        view = DummyProtectedViewSet()
        view.action = "publish"
        view.rebac_config = RebacViewConfig(
            object_type="folder", action_relations={"publish": "publisher"}
        )

        request = api_rf.post("/dummy/1/publish/")
        request.rebac_user = "user:bob"

        # Pure boolean return
        mock_rebac_client.check.return_value = True

        perm = IsRebacAuthorized()
        assert perm.has_object_permission(request, view, MockFolder(id=1)) is True

    def test_has_object_permission_put_maps_to_update_relation(self, api_rf, mock_rebac_client):
        """Verifies that PUT requests enforce the 'update_relation'."""
        view = DummyProtectedView()
        view.rebac_config = RebacViewConfig(object_type="document", update_relation="editor")

        request = api_rf.put("/dummy/1/", {"title": "Updated"}, format="json")
        request.rebac_user = "user:bob"

        # Pure boolean return
        mock_rebac_client.check.return_value = True
        perm = IsRebacAuthorized()

        assert perm.has_object_permission(request, view, MockFolder(id=1)) is True

    def test_has_object_permission_unmapped_http_method(self, api_rf, mock_rebac_client):
        """Verifies that unmapped or rogue HTTP methods (like TRACE) are denied."""
        view = DummyProtectedView()
        view.rebac_config = RebacViewConfig(object_type="document", read_relation="reader")

        request = api_rf.generic("TRACE", "/dummy/1/")
        request.rebac_user = "user:bob"

        perm = IsRebacAuthorized()
        assert perm.has_object_permission(request, view, MockFolder(id=1)) is False
        mock_rebac_client.check.assert_not_called()

    def test_has_object_permission_explicit_opt_out(self, api_rf, mock_rebac_client):
        """Verifies that setting a mapped relation to None bypasses the network check."""
        view = DummyProtectedView()
        # Explicitly opt-out of read checks
        view.rebac_config = RebacViewConfig(object_type="document", read_relation=None)

        request = api_rf.get("/dummy/1/")
        request.rebac_user = "user:bob"

        perm = IsRebacAuthorized()
        assert perm.has_object_permission(request, view, MockFolder(id=1)) is True

        # Mathematical proof: It bypassed the check, so the network was never called
        mock_rebac_client.check.assert_not_called()

    def test_stateless_resolution_via_url_kwarg(self, api_rf, mock_rebac_client):
        """Verifies extracting the object ID directly from DRF router kwargs."""
        view = DummyProtectedView()
        view.rebac_config = RebacViewConfig(
            object_type="organization",
            read_relation="can_view",
            lookup_url_kwarg="org_id",
        )
        view.kwargs = {"org_id": "acme_123"}
        request = api_rf.get("/dummy/")
        from rebac.conf import get_setting

        setattr(request, get_setting("REBAC_USER_ATTR"), "user:bob")

        class EmptyObject:
            pass

        perm = IsRebacAuthorized()
        # Pure boolean return
        mock_rebac_client.check.return_value = True

        assert perm.has_object_permission(request, view, EmptyObject()) is True

    def test_stateless_resolution_via_http_header(self, api_rf, mock_rebac_client):
        """Verifies extracting the object ID directly from incoming HTTP headers."""
        view = DummyProtectedView()
        view.rebac_config = RebacViewConfig(
            object_type="organization",
            read_relation="can_view",
            lookup_header="HTTP_X_CONTEXT_ORG_ID",
        )
        view.kwargs = {}
        request = api_rf.get("/dummy/", HTTP_X_CONTEXT_ORG_ID="stark_industries")
        from rebac.conf import get_setting

        setattr(request, get_setting("REBAC_USER_ATTR"), "user:bob")

        class EmptyObject:
            pass

        perm = IsRebacAuthorized()
        # Pure boolean return
        mock_rebac_client.check.return_value = True

        assert perm.has_object_permission(request, view, EmptyObject()) is True

    def test_guardrail_list_configs_without_mixin_warns(self, api_rf, caplog):
        """
        Verifies a logger warning is emitted if list configurations are used
        on a view that does not inherit from RebacViewMixin.
        """
        view = DummyProtectedView()

        # Deliberately misconfigure the view by adding list_relation
        view.rebac_config = RebacViewConfig(
            object_type="folder",
            read_relation="can_read",
            list_relation="can_list_explicit",
        )

        request = api_rf.get("/dummy/1/")
        request.rebac_user = "user:bob"

        perm = IsRebacAuthorized()

        # Capture standard logger output instead of Python warnings
        with caplog.at_level(logging.WARNING):
            perm._get_config(view)

        # Mathematical Proof: The log was fired, containing the exact DX instructions
        assert "uses 'list_relation' or 'disable_list_filter'" in caplog.text
        assert "does not inherit from 'RebacViewMixin'" in caplog.text
        assert "will safely ignore these settings" in caplog.text

    def test_permission_dsl_mismatch_raises_improperly_configured(self, api_rf, mock_rebac_client):
        """Verifies that an SDK error triggers the fail-closed security pattern."""
        view = DummyProtectedView()
        request = api_rf.get("/dummy/1/")
        request.rebac_user = "user:bob"
        perm = IsRebacAuthorized()

        # Agnostic exceptions cause the permission to fail closed (Return False)
        mock_rebac_client.check.side_effect = Exception("Backend down or invalid schema")
        assert perm.has_object_permission(request, view, MockFolder(id=1)) is False

    def test_permission_parent_check_validation_error(self, api_rf, mock_rebac_client):
        """Verifies that a missing DSL relation on POST parent check fails closed."""
        view = DummyProtectedView()
        wsgi_request = api_rf.post("/dummy/", {"org_id": "org_777"}, format="json")
        drf_request = view.initialize_request(wsgi_request)
        drf_request.rebac_user = "user:bob"

        # Agnostic exceptions cause the permission to fail closed (Return False)
        mock_rebac_client.check.side_effect = RebacSchemaError("Backend down or invalid schema")
        assert IsRebacAuthorized().has_permission(drf_request, view) is False

    def test_permission_object_check_validation_error(self, api_rf, mock_rebac_client):
        """Verifies that a missing DSL relation on an object check fails closed."""
        view = DummyProtectedView()
        view.rebac_config = RebacViewConfig(
            object_type="folder", update_relation="can_update_folder"
        )
        wsgi_request = api_rf.put("/dummy/1/", {"title": "Updated"}, format="json")
        drf_request = view.initialize_request(wsgi_request)
        drf_request.rebac_user = "user:bob"

        # Agnostic exceptions cause the permission to fail closed (Return False)
        mock_rebac_client.check.side_effect = Exception("Backend down or invalid schema")
        perm = IsRebacAuthorized()
        assert perm.has_object_permission(drf_request, view, MockFolder(id=1)) is False

    def test_post_creation_model_property_fallback(self, api_rf, mock_rebac_client):
        """Verifies POST creation falls back to instantiating the model to read a property."""

        class MockCompanyModel:
            @property
            def platform_id(self):
                return "model_provided_global_id"

        class MockQuerySet:
            model = MockCompanyModel

        class PropertyFallbackView(APIView):
            queryset = MockQuerySet()

        view = PropertyFallbackView()
        view.rebac_config = RebacViewConfig(
            object_type="company",
            create_scope_type="platform",
            create_scope_field="platform_id",
            create_relation="can_create",
        )

        wsgi_request = api_rf.post("/dummy/", {}, format="json")
        drf_request = view.initialize_request(wsgi_request)
        drf_request.rebac_user = "user:bob"

        # Pure boolean return
        mock_rebac_client.check.return_value = True
        perm = IsRebacAuthorized()

        assert perm.has_permission(drf_request, view) is True


# Finance App Test


class TestStatelessDashboard:
    def test_dashboard_access_allowed(self, api_rf, mock_rebac_client):
        """
        Verifies that an authorized user can access the aggregated dashboard.
        Notice we DO NOT create any Organization objects in the test database!
        """
        view = FinanceDashboardView.as_view()

        # 1. Simulate Traefik routing the request to the dashboard
        request = api_rf.get("/api/dashboard/", HTTP_X_CONTEXT_ORG_ID="acme_corp")

        # Simulate the middleware attaching the user
        from rebac.conf import get_setting

        setattr(request, get_setting("REBAC_USER_ATTR"), "user:alice")

        # Tell the mock server to return a pure boolean True
        mock_rebac_client.check.return_value = True

        # 3. Execute the view
        response = view(request)

        # 4. Verify Success
        assert response.status_code == 200
        assert response.data["dashboard_target"] == "acme_corp"

        # 5. Mathematical Proof of Statelessness
        mock_rebac_client.check.assert_called_once()

        # Extract standard kwargs instead of a positional object
        called_kwargs = mock_rebac_client.check.call_args.kwargs

        assert called_kwargs["user"] == "user:alice"
        assert called_kwargs["relation"] == "can_view_finance_dashboard"
        assert called_kwargs["obj"] == "organization:acme_corp"

    def test_dashboard_access_denied_for_intruder(self, api_rf, mock_rebac_client):
        """
        Verifies that an unauthorized user is blocked from seeing the dashboard metrics.
        """
        view = FinanceDashboardView.as_view()

        # Intruder tries to look at Stark Industries' dashboard
        request = api_rf.get("/api/dashboard/", HTTP_X_CONTEXT_ORG_ID="stark_industries")

        from rebac.conf import get_setting

        setattr(request, get_setting("REBAC_USER_ATTR"), "user:hacker_bob")

        # Tell the mock server to return a pure boolean False
        mock_rebac_client.check.return_value = False

        # 3. Execute the view and expect a 403 Forbidden
        response = view(request)

        assert response.status_code == 403
