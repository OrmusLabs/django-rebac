# tests/test_openfga_client.py
from unittest.mock import MagicMock

import pytest
from openfga_sdk.client.models import (
    ClientWriteRequestOnDuplicateWrites,
    ClientWriteRequestOnMissingDeletes,
)
from openfga_sdk.exceptions import ValidationException
from openfga_sdk.models.read_request_tuple_key import ReadRequestTupleKey

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

    def test_list_objects_strips_prefix_only_once(self, fga_backend: OpenFGABackend):
        """`removeprefix` strips exactly one leading occurrence — an ID that
        contains the type name must not be mangled by a blanket `.replace()`."""
        mock_response = MagicMock()
        mock_response.objects = ["doc:doc:1", "doc:2"]
        fga_backend.client.list_objects.return_value = mock_response

        result = fga_backend.list_objects("user:bob", "viewer", "doc")

        assert result == ["doc:1", "2"]

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

    def test_write_tuples_passes_idempotent_conflict_options(self, fga_backend: OpenFGABackend):
        """T2.6: writes must be idempotent so an outbox replay never poisons a batch."""
        fga_backend.write_tuples(
            [{"user": "user:bob", "relation": "viewer", "object": "document:1"}]
        )

        _, kwargs = fga_backend.client.write.call_args
        conflict = kwargs["options"]["conflict"]
        assert conflict.on_duplicate_writes == ClientWriteRequestOnDuplicateWrites.IGNORE
        assert conflict.on_missing_deletes == ClientWriteRequestOnMissingDeletes.IGNORE

    def test_delete_tuples_passes_idempotent_conflict_options(self, fga_backend: OpenFGABackend):
        """T2.6: deletes must ignore missing tuples so outbox replays stay idempotent."""
        fga_backend.delete_tuples(
            [{"user": "user:bob", "relation": "viewer", "object": "document:1"}]
        )

        _, kwargs = fga_backend.client.write.call_args
        conflict = kwargs["options"]["conflict"]
        assert conflict.on_duplicate_writes == ClientWriteRequestOnDuplicateWrites.IGNORE
        assert conflict.on_missing_deletes == ClientWriteRequestOnMissingDeletes.IGNORE

    # ==========================================
    # 🧪 4. BATCH CHECKS
    # ==========================================
    def test_batch_check_empty_list_returns_early(self, fga_backend: OpenFGABackend):
        """Verifies an empty batch returns an empty dictionary."""
        assert fga_backend.batch_check([]) == {}

    def test_batch_check_success_parsing(self, fga_backend: OpenFGABackend):
        """Verifies results are mapped back to the checks by request order."""
        mock_response = MagicMock()

        # OpenFGA returns results in the same order as the submitted checks —
        # the adapter must pair them positionally and only read the public
        # `allowed` attribute (no private SDK internals).
        item1 = MagicMock()
        item1.allowed = True

        item2 = MagicMock()
        item2.allowed = False

        # `ClientBatchCheckResponse` exposes the items as `.result`, not `.responses`.
        mock_response.result = [item1, item2]

        fga_backend.client.batch_check.return_value = mock_response

        checks = [
            {"user": "user:bob", "relation": "viewer", "object": "document:1"},
            {"user": "user:bob", "relation": "editor", "object": "document:1"},
        ]

        result = fga_backend.batch_check(checks)

        assert result == {"document:1": {"viewer": True, "editor": False}}

    def test_batch_check_schema_error_raises(self, fga_backend: OpenFGABackend):
        """A store-side validation failure must propagate, not degrade to {}."""
        fga_backend.client.batch_check.side_effect = ValidationException("Unknown relation")

        checks = [{"user": "user:bob", "relation": "nope", "object": "document:1"}]

        with pytest.raises(RebacSchemaError, match="Schema Mismatch"):
            fga_backend.batch_check(checks)

    def test_batch_check_network_error_raises(self, fga_backend: OpenFGABackend):
        """Transport failures must propagate so callers can fail loudly.

        Swallowing them into {} made "no permission" indistinguishable from
        "authorization backend is down" and rendered the serializer's
        except-RebacError branch unreachable.
        """
        fga_backend.client.batch_check.side_effect = Exception("SDK Batch Crash")

        checks = [{"user": "user:bob", "relation": "viewer", "object": "document:1"}]

        with pytest.raises(RebacConnectionError, match="ReBAC network error"):
            fga_backend.batch_check(checks)

    # ==========================================
    # 🧪 5. READ CONSISTENCY PREFERENCE
    # =========================================
    def test_read_options_none_by_default(self, fga_backend: OpenFGABackend):
        """T1.4: no consistency preference is forced by default (OpenFGA server default)."""
        assert fga_backend._read_options is None

    def test_check_forwards_configured_consistency(self):
        """T1.4: a configured CONSISTENCY preference is forwarded to the SDK."""
        backend = OpenFGABackend(STORE_ID="test_store_123", CONSISTENCY="HIGHER_CONSISTENCY")
        backend.client = MagicMock()
        backend.client.check.return_value.allowed = True

        backend.check("user:bob", "viewer", "document:1")

        _, kwargs = backend.client.check.call_args
        assert kwargs["options"] == {"consistency": "HIGHER_CONSISTENCY"}

    # ==========================================
    # 🧪 6. READ TUPLES
    # ==========================================
    def test_read_tuples_returns_stored_tuples(self, fga_backend: OpenFGABackend):
        """The read side of the sync contract — stored tuples come back as dicts."""
        entry = MagicMock()
        entry.key.user = "user:bob"
        entry.key.relation = "viewer"
        entry.key.object = "document:1"
        fga_backend.client.read.return_value = MagicMock(tuples=[entry], continuation_token=None)

        result = fga_backend.read_tuples("document:1")

        assert result == [{"user": "user:bob", "relation": "viewer", "object": "document:1"}]
        # The read MUST be scoped to the single object (filter), not a full-store scan.
        arg = fga_backend.client.read.call_args.args[0]
        assert isinstance(arg, ReadRequestTupleKey)
        assert arg.object == "document:1"

    def test_read_tuples_follows_continuation_token(self, fga_backend: OpenFGABackend):
        """Paginated stores are drained completely before the diff is trusted."""
        page_one = MagicMock(tuples=[], continuation_token="next-page")
        page_two = MagicMock(tuples=[], continuation_token=None)
        fga_backend.client.read.side_effect = [page_one, page_two]

        fga_backend.read_tuples("document:1")

        assert fga_backend.client.read.call_count == 2
        second_options = fga_backend.client.read.call_args_list[1].kwargs["options"]
        assert second_options == {"continuation_token": "next-page"}

    def test_read_tuples_validation_exception_raises_schema_error(
        self, fga_backend: OpenFGABackend
    ):
        """An invalid object surfaces as RebacSchemaError, not a bare SDK error."""
        fga_backend.client.read.side_effect = ValidationException("Invalid object")

        with pytest.raises(RebacSchemaError, match="ReBAC Schema Mismatch"):
            fga_backend.read_tuples("not-a-type:1")

    def test_read_tuples_network_error_raises_connection_error(self, fga_backend: OpenFGABackend):
        """A store outage surfaces as RebacConnectionError so callers can abort cleanly."""
        fga_backend.client.read.side_effect = Exception("Network timeout")

        with pytest.raises(RebacConnectionError, match="ReBAC network error"):
            fga_backend.read_tuples("document:1")

    # ==========================================
    # 🧪 7. T3.10 PRODUCTION CLIENT CONFIGURATION
    # ==========================================
    def test_setup_forwards_no_production_options_by_default(self):
        """T3.10: unset options add nothing — unauthenticated localhost keeps working."""
        from unittest.mock import patch

        with (
            patch("rebac.backends.openfga.client.ClientConfiguration") as mock_config_cls,
            patch("rebac.backends.openfga.client.OpenFgaClient"),
        ):
            OpenFGABackend(STORE_ID="test_store_123")

        kwargs = mock_config_cls.call_args.kwargs
        assert kwargs["api_url"] == "http://localhost:8080"
        assert kwargs["store_id"] == "test_store_123"
        for omitted in ("credentials", "timeout_millisec", "authorization_model_id", "ssl_ca_cert"):
            assert omitted not in kwargs

    def test_setup_forwards_api_token_credentials(self):
        """T3.10: api_token auth (FGA managed service / secured self-host)."""
        from unittest.mock import patch

        from openfga_sdk.credentials import Credentials

        with (
            patch("rebac.backends.openfga.client.ClientConfiguration") as mock_config_cls,
            patch("rebac.backends.openfga.client.OpenFgaClient"),
        ):
            OpenFGABackend(
                STORE_ID="s",
                CREDENTIALS={"method": "api_token", "api_token": "fga_secret_token"},
            )

        creds = mock_config_cls.call_args.kwargs["credentials"]
        assert isinstance(creds, Credentials)
        assert creds.method == "api_token"
        assert creds.configuration.api_token == "fga_secret_token"

    def test_setup_forwards_client_credentials(self):
        """T3.10: client_credentials (OAuth2) auth for Auth0 FGA et al."""
        from unittest.mock import patch

        from openfga_sdk.credentials import Credentials

        raw = {
            "method": "client_credentials",
            "client_id": "cid",
            "client_secret": "csecret",
            "api_issuer": "https://auth.example.com",
            "api_audience": "https://fga.example.com",
            "scopes": "openid profile",
        }
        with (
            patch("rebac.backends.openfga.client.ClientConfiguration") as mock_config_cls,
            patch("rebac.backends.openfga.client.OpenFgaClient"),
        ):
            OpenFGABackend(STORE_ID="s", CREDENTIALS=raw)

        creds = mock_config_cls.call_args.kwargs["credentials"]
        assert isinstance(creds, Credentials)
        assert creds.method == "client_credentials"
        assert creds.configuration.client_id == "cid"
        assert creds.configuration.client_secret == "csecret"
        assert creds.configuration.api_issuer == "https://auth.example.com"
        assert creds.configuration.api_audience == "https://fga.example.com"
        assert creds.configuration.scopes == "openid profile"

    @pytest.mark.parametrize(
        ("credentials", "message"),
        [
            ({"method": "magic_token"}, "method 'magic_token' is invalid"),
            ({"method": "api_token"}, "requires a non-empty 'api_token'"),
            ({"method": "client_credentials"}, "requires a non-empty 'client_id'"),
            (
                {
                    "method": "client_credentials",
                    "client_id": "cid",
                    "client_secret": "csecret",
                },
                "requires a non-empty 'api_issuer'",
            ),
            ("not-a-dict", "must be a dict"),
        ],
    )
    def test_setup_rejects_malformed_credentials(self, credentials, message):
        """T3.10: bad CREDENTIALS fail fast at startup, not per request."""
        from rebac.backends.openfga.exceptions import OpenFGAConfigurationError

        with pytest.raises(OpenFGAConfigurationError, match=message):
            OpenFGABackend(STORE_ID="s", CREDENTIALS=credentials)

    def test_setup_forwards_timeout_millisec(self):
        """T3.10: the timeout bound for T2.8's network call."""
        from unittest.mock import patch

        with (
            patch("rebac.backends.openfga.client.ClientConfiguration") as mock_config_cls,
            patch("rebac.backends.openfga.client.OpenFgaClient"),
        ):
            OpenFGABackend(STORE_ID="s", TIMEOUT_MILLISEC=5000)

        assert mock_config_cls.call_args.kwargs["timeout_millisec"] == 5000

    def test_setup_forwards_authorization_model_id(self):
        """T3.10: model pinning — the safe DSL-migration mechanism."""
        from unittest.mock import patch

        with (
            patch("rebac.backends.openfga.client.ClientConfiguration") as mock_config_cls,
            patch("rebac.backends.openfga.client.OpenFgaClient"),
        ):
            OpenFGABackend(STORE_ID="s", AUTHORIZATION_MODEL_ID="01J8R5QJQ5C2H6WVRPVBZJ3DRE")

        assert (
            mock_config_cls.call_args.kwargs["authorization_model_id"]
            == "01J8R5QJQ5C2H6WVRPVBZJ3DRE"
        )

    def test_setup_forwards_ssl_ca_cert(self):
        """T3.10: private CA / mTLS support."""
        from unittest.mock import patch

        with (
            patch("rebac.backends.openfga.client.ClientConfiguration") as mock_config_cls,
            patch("rebac.backends.openfga.client.OpenFgaClient"),
        ):
            OpenFGABackend(STORE_ID="s", SSL_CA_CERT="/etc/ssl/private-ca.pem")

        assert mock_config_cls.call_args.kwargs["ssl_ca_cert"] == "/etc/ssl/private-ca.pem"

    def test_setup_forwards_retry_params(self):
        """T3.10: SDK-level retry of transient 429/5xx before the outbox retry loop."""
        from unittest.mock import patch

        from openfga_sdk.configuration import RetryParams

        with (
            patch("rebac.backends.openfga.client.ClientConfiguration") as mock_config_cls,
            patch("rebac.backends.openfga.client.OpenFgaClient"),
        ):
            OpenFGABackend(
                STORE_ID="s",
                RETRY_PARAMS={"max_retry": 5, "min_wait_in_ms": 200, "max_wait_in_sec": 30},
            )

        retry_params = mock_config_cls.call_args.kwargs["retry_params"]
        assert isinstance(retry_params, RetryParams)
        assert retry_params.max_retry == 5
        assert retry_params.min_wait_in_ms == 200
        assert retry_params.max_wait_in_sec == 30

    def test_setup_rejects_malformed_retry_params(self):
        """T3.10: RETRY_PARAMS must be a dict."""
        from rebac.backends.openfga.exceptions import OpenFGAConfigurationError

        with pytest.raises(OpenFGAConfigurationError, match="must be a dict"):
            OpenFGABackend(STORE_ID="s", RETRY_PARAMS="five")
