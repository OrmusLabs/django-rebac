import pytest
from rest_framework import generics
from rest_framework.exceptions import APIException
from rest_framework.request import Request

from rebac.core.structs import RebacViewConfig
from rebac.permissions import IsRebacAuthorized
from rebac.views.mixins import RebacViewMixin
from tests.models import MockFolder

pytestmark = pytest.mark.django_db

# ==========================================
# 🛠️ SHARED CONFIGURATION & FIXTURES
# ==========================================

SHARED_rebac_config = RebacViewConfig(
    object_type="folder",
    read_relation="can_read",
    update_relation="can_edit",
    delete_relation="can_delete",
    create_scope_type="organization",
    create_scope_field="org_id",
    create_relation="can_create_folders",
)


class PermissionShieldedView(generics.GenericAPIView):
    """View utilizing the decoupled Permission Class approach."""

    queryset = MockFolder.objects.all()
    permission_classes = (IsRebacAuthorized,)
    rebac_config = SHARED_rebac_config


class MixinShieldedView(RebacViewMixin, generics.GenericAPIView):
    """View utilizing the unified Mixin approach."""

    queryset = MockFolder.objects.all()
    rebac_config = SHARED_rebac_config


# Tuple containing the uninstantiated view classes for parameterized testing
VIEW_STRATEGIES = [PermissionShieldedView, MixinShieldedView]


# ==========================================
# 🧪 PARITY TEST SUITE
# ==========================================


class TestAuthorizationParity:
    """Verifies behavioral parity between IsRebacAuthorized and RebacViewMixin."""

    def _prepare_drf_request(
        self, view_class: type, wsgi_request
    ) -> tuple[generics.GenericAPIView, Request]:
        """Helper to fully initialize a DRF request with parsers and authenticators."""
        view = view_class()
        view.kwargs = {}  # Mock the kwargs normally injected by the DRF router
        drf_request = view.initialize_request(wsgi_request)
        view.request = drf_request
        return view, drf_request

    @pytest.mark.parametrize("view_class", VIEW_STRATEGIES, ids=["PermissionClass", "Mixin"])
    def test_parity_missing_identity_is_rejected(self, api_rf, view_class):
        """Both strategies MUST safely reject requests missing the `rebac_user` context."""
        wsgi_request = api_rf.post("/dummy/", {"org_id": "org_123"}, format="json")
        view, drf_request = self._prepare_drf_request(view_class, wsgi_request)

        # We explicitly DO NOT attach drf_request.rebac_user here

        # Both implementations should raise an APIException (401 or 403)
        with pytest.raises(APIException):
            view.check_permissions(drf_request)

    @pytest.mark.parametrize("view_class", VIEW_STRATEGIES, ids=["PermissionClass", "Mixin"])
    def test_parity_post_parent_check_allowed(self, api_rf, mock_rebac_client, view_class):
        """Both strategies MUST execute a parent check on POST and pass if FGA allows."""
        wsgi_request = api_rf.post("/dummy/", {"org_id": "org_123"}, format="json")
        view, drf_request = self._prepare_drf_request(view_class, wsgi_request)
        drf_request.rebac_user = "user:bob"

        mock_rebac_client.check.return_value = True

        # Should NOT raise any exceptions
        view.check_permissions(drf_request)

        # Verify exact same network payload was sent
        mock_rebac_client.check.assert_called_once()

        # Extract kwargs and assert dictionary keys
        called_kwargs = mock_rebac_client.check.call_args.kwargs
        assert called_kwargs["user"] == "user:bob"
        assert called_kwargs["relation"] == "can_create_folders"
        assert called_kwargs["obj"] == "organization:org_123"

    @pytest.mark.parametrize("view_class", VIEW_STRATEGIES, ids=["PermissionClass", "Mixin"])
    def test_parity_post_parent_check_denied(self, api_rf, mock_rebac_client, view_class):
        """Both strategies MUST reject the request if FGA blocks parent creation."""
        wsgi_request = api_rf.post("/dummy/", {"org_id": "org_123"}, format="json")
        view, drf_request = self._prepare_drf_request(view_class, wsgi_request)
        drf_request.rebac_user = "user:mallory"

        # Return a pure boolean False!
        mock_rebac_client.check.return_value = False

        with pytest.raises(APIException):
            view.check_permissions(drf_request)

    @pytest.mark.parametrize("view_class", VIEW_STRATEGIES, ids=["PermissionClass", "Mixin"])
    def test_parity_object_update_denied(self, api_rf, mock_rebac_client, view_class):
        """Both strategies MUST enforce the update_relation on PUT methods."""
        folder = MockFolder.objects.create(name="Top Secret", org_id="o1", creator_id="u1")

        wsgi_request = api_rf.put(f"/dummy/{folder.id}/", {"name": "Hacked"}, format="json")
        view, drf_request = self._prepare_drf_request(view_class, wsgi_request)
        drf_request.rebac_user = "user:mallory"

        # Return a pure boolean, not an OpenFGA object
        mock_rebac_client.check.return_value = False

        with pytest.raises(APIException):
            view.check_object_permissions(drf_request, folder)

        # Check standard kwargs, not a ClientCheckRequest object
        called_kwargs = mock_rebac_client.check.call_args.kwargs
        assert called_kwargs["relation"] == "can_edit"
        assert called_kwargs["user"] == "user:mallory"

    @pytest.mark.parametrize("view_class", VIEW_STRATEGIES, ids=["PermissionClass", "Mixin"])
    def test_parity_explicit_bypass_none_relation(self, api_rf, mock_rebac_client, view_class):
        """Both strategies MUST bypass FGA network checks if the relation is None."""
        folder = MockFolder.objects.create(name="Public", org_id="o1", creator_id="u1")

        wsgi_request = api_rf.get(f"/dummy/{folder.id}/")
        view, drf_request = self._prepare_drf_request(view_class, wsgi_request)
        drf_request.rebac_user = "user:bob"

        # Mutate the configuration at the instance level to explicitly bypass read checks
        view.rebac_config = RebacViewConfig(
            object_type="folder",
            read_relation=None,  # Explicit Opt-Out
        )

        # Execute check
        view.check_object_permissions(drf_request, folder)

        # Mathematical proof of parity: The FGA client MUST NOT have been called
        mock_rebac_client.check.assert_not_called()
