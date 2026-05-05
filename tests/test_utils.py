# tests/test_utils.py
import pytest
from django.core.exceptions import ImproperlyConfigured

from rebac.conf import get_setting
from rebac.utils import get_rebac_client


class TestConfigurationAndUtils:
    def test_get_setting_fallback(self):
        """Verifies that missing settings fall back to DEFAULTS."""
        assert get_setting("BATCH_SIZE") == 50

    def test_get_invalid_setting_raises_error(self):
        """Verifies that asking for a non-existent setting crashes safely."""
        with pytest.raises(ImproperlyConfigured):
            get_setting("SOME_FAKE_SETTING")

    def test_missing_store_id_raises_error(self, settings):
        """Verifies the client refuses to instantiate without a Store ID."""
        # Wipe out the correct settings namespace
        settings.REBAC_CONFIG = {}

        # Clear the lru_cache on the function so it executes freshly
        get_rebac_client.cache_clear()

        with pytest.raises(ImproperlyConfigured) as exc_info:
            get_rebac_client()

        assert "STORE_ID" in str(exc_info.value)
