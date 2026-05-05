# tests/test_openfga_client.py
from unittest.mock import MagicMock

import pytest
from openfga_sdk.exceptions import ValidationException

from rebac.backends.base.exceptions import RebacConnectionError, RebacSchemaError
from rebac.backends.openfga.client import OpenFGABackend


@pytest.fixture
def fga_backend():
    """
    Instantiates the concrete OpenFGABackend adapter but mocks
    the underlying OpenFGA SDK client to prevent network calls.
    """
    backend = OpenFGABackend(STORE_ID="test_store_123")
    backend.client = MagicMock()
    return backend


class TestOpenFGABackend:
    # ==========================================
    # 🧪 1. CHECK EXCEPTIONS
    # ==========================================
    def test_check_validation_exception_raises_schema_error(self, fga_backend: OpenFGABackend):
        """Verifies DSL mismatch errors are correctly translated to RebacSchemaError."""
        fga_backend.client.check.side_effect = ValidationException("Invalid relation")

        with pytest.raises(RebacSchemaError, match="ReBAC Schema Mismatch"):
            fga_backend.check("user:bob", "fake_relation", "document:1")

    def test_check_generic_exception_raises_connection_error(self, fga_backend: OpenFGABackend):
        """Verifies network failures are translated to RebacConnectionError."""
        fga_backend.client.check.side_effect = Exception("Network timeout")

        with pytest.raises(RebacConnectionError, match="ReBAC network error"):
            fga_backend.check("user:bob", "viewer", "document:1")

    # ==========================================
    # 🧪 2. LIST OBJECTS
    # ==========================================
    def test_list_objects_success_strips_prefix(self, fga_backend: OpenFGABackend):
        """Verifies raw IDs are successfully returned without the type prefix."""
        mock_response = MagicMock()
        # SDK returns prefixed strings
        mock_response.objects = ["document:10", "document:25"]
        fga_backend.client.list_objects.return_value = mock_response

        result = fga_backend.list_objects("user:bob", "viewer", "document")

        assert result == ["10", "25"]
        fga_backend.client.list_objects.assert_called_once()

    def test_list_objects_validation_exception_returns_empty(self, fga_backend: OpenFGABackend):
        """Verifies querying a non-existent relation safely returns an empty list."""
        fga_backend.client.list_objects.side_effect = ValidationException("Invalid relation")

        result = fga_backend.list_objects("user:bob", "fake_relation", "document")
        assert result == []

    def test_list_objects_generic_exception_raises_connection_error(
        self, fga_backend: OpenFGABackend
    ):
        """Verifies network failures during list_objects raise a RebacConnectionError."""
        fga_backend.client.list_objects.side_effect = Exception("Network timeout")

        with pytest.raises(RebacConnectionError, match="ReBAC network error"):
            fga_backend.list_objects("user:bob", "viewer", "document")

    # ==========================================
    # 🧪 3. WRITE & DELETE EARLY RETURNS
    # ==========================================
    def test_write_tuples_empty_list_returns_early(self, fga_backend: OpenFGABackend):
        """Verifies passing an empty list bypasses SDK execution."""
        fga_backend.write_tuples([])
        fga_backend.client.write.assert_not_called()

    def test_delete_tuples_empty_list_returns_early(self, fga_backend: OpenFGABackend):
        """Verifies passing an empty list bypasses SDK execution."""
        fga_backend.delete_tuples([])
        fga_backend.client.write.assert_not_called()

    def test_write_tuples_success(self, fga_backend: OpenFGABackend):
        """Verifies valid dictionaries are translated to ClientWriteRequests."""
        tuples = [{"user": "user:bob", "relation": "viewer", "object": "document:1"}]
        fga_backend.write_tuples(tuples)

        fga_backend.client.write.assert_called_once()

    def test_delete_tuples_success(self, fga_backend: OpenFGABackend):
        """Verifies valid dictionaries are translated to ClientWriteRequests."""
        tuples = [{"user": "user:bob", "relation": "viewer", "object": "document:1"}]
        fga_backend.delete_tuples(tuples)

        fga_backend.client.write.assert_called_once()

    # ==========================================
    # 🧪 4. BATCH CHECKS
    # ==========================================
    def test_batch_check_empty_list_returns_early(self, fga_backend: OpenFGABackend):
        """Verifies an empty batch returns an empty dictionary."""
        assert fga_backend.batch_check([]) == {}

    def test_batch_check_success_parsing(self, fga_backend: OpenFGABackend):
        """Verifies the adapter correctly parses the OpenFGA Batch Response tree."""
        mock_response = MagicMock()

        # Simulated Item 1: Bob is a viewer (using `_request`)
        item1 = MagicMock()
        item1.allowed = True
        req1 = MagicMock()
        req1.object = "document:1"
        req1.relation = "viewer"
        item1._request = req1

        # Simulated Item 2: Bob is NOT an editor (using `request` fallback)
        item2 = MagicMock()
        item2.allowed = False
        req2 = MagicMock()
        req2.object = "document:1"
        req2.relation = "editor"
        # Delete the attribute entirely so getattr() triggers its fallback!
        del item2._request
        item2.request = req2

        # Simulated Item 3: Corrupted response item (Should be safely ignored via `continue`)
        item3 = MagicMock()
        item3._request = None
        item3.request = None

        mock_response.responses = [item1, item2, item3]
        fga_backend.client.batch_check.return_value = mock_response

        checks = [
            {"user": "user:bob", "relation": "viewer", "object": "document:1"},
            {"user": "user:bob", "relation": "editor", "object": "document:1"},
        ]

        result = fga_backend.batch_check(checks)

        assert result == {"document:1": {"viewer": True, "editor": False}}

    def test_batch_check_network_error_fails_gracefully(self, fga_backend: OpenFGABackend):
        """Verifies network failures during batches return an empty dictionary safely."""
        fga_backend.client.batch_check.side_effect = Exception("SDK Batch Crash")

        checks = [{"user": "user:bob", "relation": "viewer", "object": "document:1"}]
        result = fga_backend.batch_check(checks)

        assert result == {}
