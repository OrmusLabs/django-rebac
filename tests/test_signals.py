# tests/test_signals.py
from unittest.mock import patch

from django.core.signals import setting_changed


class TestRebacSignals:
    @patch("rebac.signals.get_rebac_client.cache_clear")
    def test_clear_fga_client_cache_on_rebac_config_change(self, mock_cache_clear) -> None:
        """
        Verifies that mutating the REBAC_CONFIG setting successfully triggers
        the cache flush for the underlying ReBAC client.
        """
        # Act: Manually fire Django's built-in setting_changed signal
        setting_changed.send(
            sender=None, setting="REBAC_CONFIG", value={"NEW": "CONFIG"}, enter=True
        )

        # Assert: Mathematical Proof the cache was wiped
        mock_cache_clear.assert_called_once()

    @patch("rebac.signals.get_rebac_client.cache_clear")
    def test_clear_fga_client_cache_ignores_unrelated_settings(self, mock_cache_clear) -> None:
        """
        Verifies that modifying completely unrelated Django settings
        does not cause unnecessary cache invalidation.
        """
        # Act: Fire the signal for a different setting
        setting_changed.send(sender=None, setting="DATABASES", value={}, enter=True)

        # Assert: The guard clause safely ignored it
        mock_cache_clear.assert_not_called()
