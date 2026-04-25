# tests/test_conf.py
import logging

import pytest
from django.core.exceptions import ImproperlyConfigured

from rebac.conf import validate_settings


class TestSettingsValidation:
    def test_validate_settings_handshake_failure(self, mocker):
        """Verifies that a mismatch between header targets and the user attribute crashes safely."""

        # Mock settings to create a mismatch: mapping targets "rebac_tenant",
        # but expecting "rebac_user"
        def mock_setting(key):
            if key == "REBAC_USER_ATTR":
                return "rebac_user"
            elif key == "REQUEST_HEADER_MAPPINGS":
                return {"X-User-Id": "rebac_tenant"}
            return "user:"

        mocker.patch("rebac.conf.get_setting", side_effect=mock_setting)

        with pytest.raises(ImproperlyConfigured, match="never be able to set the user context"):
            validate_settings()

    def test_validate_settings_missing_prefix_colon(self, mocker, caplog):
        """Verifies that a missing colon in the prefix throws a Developer Experience log warning."""

        def mock_setting(key):
            if key == "REBAC_USER_PREFIX":
                return "user"  # Missing the colon!
            elif key == "REQUEST_HEADER_MAPPINGS":
                return {"X-User-Id": "rebac_user"}
            return "rebac_user"

        # Mock get_setting so we can inject our bad configuration
        mocker.patch("rebac.conf.get_setting", side_effect=mock_setting)

        # Listen to the logger at the WARNING level, not pytest.warns
        with caplog.at_level(logging.WARNING):
            validate_settings()

        # Assert that our DX logger printed the correct message to the console
        assert "does not end with a colon" in caplog.text
        assert "REBAC_USER_PREFIX" in caplog.text
