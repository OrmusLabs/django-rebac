# tests/test_middleware.py
from unittest.mock import MagicMock, patch

from rebac.middleware import GatewayIdentityMiddleware


class TestGatewayIdentityMiddleware:
    def test_middleware_attaches_rebac_user_when_header_present(self):
        """Verifies that the X-User-Id header is correctly mapped to request.rebac_user."""
        mock_get_response = MagicMock(return_value="response")
        middleware = GatewayIdentityMiddleware(mock_get_response)

        # Create a dummy request with the Traefik header
        mock_request = MagicMock()
        mock_request.headers = {"X-User-Id": "123-abc"}

        response = middleware(mock_request)

        assert mock_request.rebac_user == "user:123-abc"
        assert response == "response"

    def test_middleware_sets_none_when_header_missing(self):
        """Verifies the middleware safely handles missing identities."""
        mock_get_response = MagicMock(return_value="response")
        middleware = GatewayIdentityMiddleware(mock_get_response)

        mock_request = MagicMock()
        mock_request.headers = {}

        middleware(mock_request)

        assert mock_request.rebac_user is None

    @patch("rebac.middleware.settings")
    @patch("rebac.middleware.get_setting")
    def test_fallback_django_user_in_debug(self, mock_get_setting, mock_settings):
        """Verifies fallback to authenticated Django user when DEBUG is True."""
        mock_settings.DEBUG = True

        # Mock configuration specifically for this test
        mock_get_setting.side_effect = lambda key: {
            "REQUEST_HEADER_MAPPINGS": {"X-User-Id": "rebac_user"},
            "REBAC_USER_ATTR": "rebac_user",
            "REBAC_USER_PREFIX": "user:",
            "LOCAL_DEV_FALLBACK": {"USE_DJANGO_USER": True, "STATIC_USER_ID": None},
        }.get(key)

        mock_get_response = MagicMock()
        middleware = GatewayIdentityMiddleware(mock_get_response)

        mock_request = MagicMock()
        mock_request.headers = {}  # Header missing to trigger fallback

        # Simulate an authenticated Django session user
        mock_request.user.is_authenticated = True
        mock_request.user.id = 999

        middleware(mock_request)

        assert mock_request.rebac_user == "user:999"

    @patch("rebac.middleware.settings")
    @patch("rebac.middleware.get_setting")
    def test_fallback_static_user_in_debug(self, mock_get_setting, mock_settings):
        """Verifies fallback to STATIC_USER_ID when DEBUG is True."""
        mock_settings.DEBUG = True

        # Turn off Django user, turn on Static User
        mock_get_setting.side_effect = lambda key: {
            "REQUEST_HEADER_MAPPINGS": {"X-User-Id": "rebac_user"},
            "REBAC_USER_ATTR": "rebac_user",
            "REBAC_USER_PREFIX": "user:",
            "LOCAL_DEV_FALLBACK": {"USE_DJANGO_USER": False, "STATIC_USER_ID": "dev-static-123"},
        }.get(key)

        mock_get_response = MagicMock()
        middleware = GatewayIdentityMiddleware(mock_get_response)

        mock_request = MagicMock()
        mock_request.headers = {}

        middleware(mock_request)

        assert mock_request.rebac_user == "user:dev-static-123"

    @patch("rebac.middleware.settings")
    @patch("rebac.middleware.get_setting")
    def test_no_fallback_when_debug_false(self, mock_get_setting, mock_settings):
        """Verifies fallbacks are completely ignored in production (DEBUG=False)."""
        mock_settings.DEBUG = False

        # Configure fallbacks as active, to prove they are ignored by the DEBUG flag
        mock_get_setting.side_effect = lambda key: {
            "REQUEST_HEADER_MAPPINGS": {"X-User-Id": "rebac_user"},
            "rebac_user_ATTR": "rebac_user",
            "rebac_user_PREFIX": "user:",
            "LOCAL_DEV_FALLBACK": {"USE_DJANGO_USER": True, "STATIC_USER_ID": "dev-static-123"},
        }.get(key)

        mock_get_response = MagicMock()
        middleware = GatewayIdentityMiddleware(mock_get_response)

        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.user.is_authenticated = True
        mock_request.user.id = 999

        middleware(mock_request)

        # Thanks to the defensive setup, it remains cleanly initialized as None!
        assert mock_request.rebac_user is None

    @patch("rebac.middleware.get_setting")
    def test_prefix_applied_only_to_rebac_user_attr(self, mock_get_setting):
        """Verifies the prefix is ONLY applied to the designated user attribute,
        not other context.
        """

        # Configure multiple header mappings to test the branching logic in Step 4
        mock_get_setting.side_effect = lambda key: {
            "REQUEST_HEADER_MAPPINGS": {
                "X-User-Id": "rebac_user",
                "X-Context-Org-Id": "rebac_tenant",
                "X-Department-Id": "rebac_department",
            },
            "REBAC_USER_ATTR": "rebac_user",
            "REBAC_USER_PREFIX": "user:",
            "LOCAL_DEV_FALLBACK": {},
        }.get(key)

        mock_get_response = MagicMock()
        middleware = GatewayIdentityMiddleware(mock_get_response)

        # Simulate Traefik passing multiple context headers
        mock_request = MagicMock()
        mock_request.headers = {
            "X-User-Id": "123-abc",
            "X-Context-Org-Id": "acme-corp",
            "X-Department-Id": "engineering",
        }

        middleware(mock_request)

        # 1. The primary FGA user attribute MUST get the prefix
        assert mock_request.rebac_user == "user:123-abc"

        # 2. Other mapped attributes MUST NOT get the prefix
        assert mock_request.rebac_tenant == "acme-corp"
        assert mock_request.rebac_department == "engineering"
