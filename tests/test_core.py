# tests/test_core.py
import logging

import pytest
from django.core.exceptions import ImproperlyConfigured

from rebac.common.loggers import RebacConsoleLogger
from rebac.models import RebacSyncOutbox
from rebac.utils import get_rebac_client

pytestmark = pytest.mark.django_db


class TestCoreComponents:
    def test_outbox_string_representation(self):
        """Verifies the __str__ method of the Outbox model."""
        task = RebacSyncOutbox(
            action=RebacSyncOutbox.Action.WRITE,
            relation="viewer",
            object_id="document:1",
            status=RebacSyncOutbox.Status.PENDING,
        )
        assert str(task) == "WRT viewer for document:1 (PEND)"

    def test_console_logger_info_and_debug(self, caplog):
        """Verifies the info and debug methods of the DX Logger."""
        logger = RebacConsoleLogger("test_logger")

        with caplog.at_level(logging.DEBUG):
            logger.info("Information here")
            logger.debug("Debugging here")

        assert "💡 ReBAC INFO: Information here" in caplog.text
        assert "🔍 ReBAC DEBUG: Debugging here" in caplog.text

    def test_fga_client_initialization_error(self, settings, mocker):
        """Verifies that SDK instantiation errors are wrapped safely."""
        settings.REBAC_CONFIG = {
            "BACKEND_OPTIONS": {
                "STORE_ID": "123",
                "API_URL": "http://localhost:8080",
            }
        }
        get_rebac_client.cache_clear()

        # Force the OpenFGA SDK to fail during initialization
        mocker.patch(
            "rebac.backends.openfga.client.OpenFgaClient", side_effect=ValueError("Bad URL")
        )

        # Expect Django's ImproperlyConfigured exception

        with pytest.raises(ImproperlyConfigured, match="Failed to initialize ReBAC backend"):
            get_rebac_client()
