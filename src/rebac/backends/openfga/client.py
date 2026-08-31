# django_rebac/backends/openfga/client.py
import logging
from typing import Any

from openfga_sdk.client import ClientConfiguration
from openfga_sdk.client.models import (
    ClientBatchCheckItem,
    ClientBatchCheckRequest,
    ClientCheckRequest,
    ClientListObjectsRequest,
    ClientTuple,
    ClientWriteRequest,
    ClientWriteRequestOnDuplicateWrites,
    ClientWriteRequestOnMissingDeletes,
    ConflictOptions,
)
from openfga_sdk.configuration import RetryParams
from openfga_sdk.credentials import CredentialConfiguration, Credentials
from openfga_sdk.exceptions import ValidationException
from openfga_sdk.models.read_request_tuple_key import ReadRequestTupleKey
from openfga_sdk.sync import OpenFgaClient

from rebac.backends.base.client import BaseReBACBackend
from rebac.backends.base.exceptions import RebacConnectionError, RebacSchemaError

from .exceptions import OpenFGAConfigurationError

logger = logging.getLogger(__name__)

# T2.6: The outbox guarantees at-least-once delivery, so every write/delete MUST be
# idempotent. We pass conflict options to the SDK (which attaches them to each tuple
# key while keeping the request atomic) so that replaying an already-applied tuple is
# a no-op instead of a hard error. Without this, a single transient failure after a
# partial success permanently poisons the batch on every subsequent retry.
_IDEMPOTENT_WRITE_OPTIONS: dict[str, Any] = {
    "conflict": ConflictOptions(
        on_duplicate_writes=ClientWriteRequestOnDuplicateWrites.IGNORE,
        on_missing_deletes=ClientWriteRequestOnMissingDeletes.IGNORE,
    )
}


class OpenFGABackend(BaseReBACBackend):
    """OpenFGA implementation of the ReBAC Backend contract.

    This adapter translates abstract ReBAC operations into strict OpenFGA SDK calls.
    It expects `API_URL` and `STORE_ID` to be provided in the backend options.

    T3.10: the full SDK configuration surface is now plumbed from BACKEND_OPTIONS
    (with safe defaults): `CREDENTIALS` (api_token / client_credentials),
    `AUTHORIZATION_MODEL_ID` (model pinning for safe DSL migration),
    `TIMEOUT_MILLISEC`, `RETRY_PARAMS`, and `SSL_CA_CERT` (private CA / mTLS).
    """

    def setup(self) -> None:
        """Instantiates the OpenFGA SDK Client.

        Raises:
            ReBACConfigurationError: If the `STORE_ID` is missing, the `CREDENTIALS`
                option is malformed, or the client fails to initialize.
        """
        api_url: str = self.options.get("API_URL", "http://localhost:8080")
        store_id: str | None = self.options.get("STORE_ID")

        if not store_id:
            raise OpenFGAConfigurationError(
                "OpenFGABackend requires a 'STORE_ID' in REBAC['BACKEND_OPTIONS']."
            )

        # T3.10: forward the full production configuration to the SDK. Options left
        # unset (None) keep the SDK's own defaults, so this changes nothing for
        # unauthenticated localhost deployments.
        config_kwargs: dict[str, Any] = {"api_url": api_url, "store_id": store_id}

        model_id = self.options.get("AUTHORIZATION_MODEL_ID")
        if model_id:
            config_kwargs["authorization_model_id"] = model_id

        timeout = self.options.get("TIMEOUT_MILLISEC")
        if timeout is not None:
            config_kwargs["timeout_millisec"] = timeout

        ca_cert = self.options.get("SSL_CA_CERT")
        if ca_cert:
            config_kwargs["ssl_ca_cert"] = ca_cert

        credentials = self._build_credentials()
        if credentials is not None:
            config_kwargs["credentials"] = credentials

        retry_params = self._build_retry_params()
        if retry_params is not None:
            config_kwargs["retry_params"] = retry_params

        # T1.4: Optional OpenFGA read consistency preference. `None` keeps the OpenFGA
        # server default (HIGHER_CONSISTENCY for check/list_objects); set
        # "MINIMAL_CONSISTENCY" in BACKEND_OPTIONS to trade freshness for latency on
        # high-traffic list endpoints.
        consistency = self.options.get("CONSISTENCY")
        self._read_options: dict[str, str] | None = (
            {"consistency": consistency} if consistency else None
        )

        config = ClientConfiguration(**config_kwargs)

        try:
            self.client = OpenFgaClient(config)
        except (ValueError, TypeError) as e:
            raise OpenFGAConfigurationError(f"Failed to initialize OpenFGA client: {e}") from e

    def _build_credentials(self) -> Credentials | None:
        """Builds the SDK `Credentials` object from the `CREDENTIALS` option (T3.10).

        Accepts a plain dict so secrets live in Django settings / env vars, not code:
            {"method": "api_token", "api_token": "fga_..."}
            {"method": "client_credentials", "client_id": "...", "client_secret": "...",
             "api_issuer": "https://auth.example.com", "api_audience": "...",
             "scopes": "openid profile"}

        Returns:
            Credentials | None: None when the option is unset (unauthenticated mode).

        Raises:
            OpenFGAConfigurationError: If the method is unknown or a required
                credential part is missing (fail fast at startup, not per request).
        """
        raw = self.options.get("CREDENTIALS")
        if not raw:
            return None

        if not isinstance(raw, dict):
            raise OpenFGAConfigurationError(
                "OpenFGA 'CREDENTIALS' must be a dict, "
                f"got {type(raw).__name__}. Expected e.g. "
                "{'method': 'api_token', 'api_token': '...'}."
            )

        method = raw.get("method")
        if method not in ("api_token", "client_credentials"):
            raise OpenFGAConfigurationError(
                f"OpenFGA 'CREDENTIALS' method '{method}' is invalid; it must be "
                "'api_token' or 'client_credentials'."
            )

        if method == "api_token":
            api_token = raw.get("api_token")
            if not api_token:
                raise OpenFGAConfigurationError(
                    "OpenFGA 'CREDENTIALS' with method 'api_token' requires a "
                    "non-empty 'api_token' value."
                )
            configuration = CredentialConfiguration(api_token=api_token)
        else:  # client_credentials
            for required in ("client_id", "client_secret", "api_issuer"):
                if not raw.get(required):
                    raise OpenFGAConfigurationError(
                        f"OpenFGA 'CREDENTIALS' with method 'client_credentials' "
                        f"requires a non-empty '{required}' value."
                    )
            configuration = CredentialConfiguration(
                client_id=raw["client_id"],
                client_secret=raw["client_secret"],
                api_issuer=raw["api_issuer"],
                api_audience=raw.get("api_audience") or None,
                scopes=raw.get("scopes"),
            )

        return Credentials(method=method, configuration=configuration)

    def _build_retry_params(self) -> RetryParams | None:
        """Builds the SDK `RetryParams` from the `RETRY_PARAMS` option (T3.10).

        Returns:
            RetryParams | None: None when unset (the SDK then uses its own default
                RetryParams(max_retry=3, min_wait_in_ms=100, max_wait_in_sec=120)).

        Raises:
            OpenFGAConfigurationError: If the option is not a dict.
        """
        raw = self.options.get("RETRY_PARAMS")
        if not raw:
            return None

        if not isinstance(raw, dict):
            raise OpenFGAConfigurationError(
                "OpenFGA 'RETRY_PARAMS' must be a dict, "
                f"got {type(raw).__name__}. Expected e.g. "
                "{'max_retry': 3, 'min_wait_in_ms': 100, 'max_wait_in_sec': 120}."
            )

        return RetryParams(
            max_retry=int(raw.get("max_retry", 3)),
            min_wait_in_ms=int(raw.get("min_wait_in_ms", 100)),
            max_wait_in_sec=int(raw.get("max_wait_in_sec", 120)),
        )

    def check(self, user: str, relation: str, obj: str) -> bool:
        """Verifies if a user has a specific relation to an object.

        Args:
            user: The subject string (e.g., 'user:123').
            relation: The action or role (e.g., 'viewer').
            obj: The resource string (e.g., 'document:456').

        Returns:
            bool: True if authorized, False otherwise.

        Raises:
            ReBACConfigurationError: If there is a DSL schema mismatch.
        """
        try:
            response = self.client.check(
                ClientCheckRequest(
                    user=user,
                    relation=relation,
                    object=obj,
                ),
                options=self._read_options,
            )
            return bool(response.allowed)
        except ValidationException as e:
            error_msg = (
                f"ReBAC Schema Mismatch: The relation '{relation}' does not exist "
                f"on the target object type in your authorization model."
            )
            logger.error(error_msg)
            # Raise the Abstract Exception
            raise RebacSchemaError(error_msg) from e
        except Exception as e:
            logger.error(f"ReBAC network or execution error during check: {e}")
            # Raise the Abstract Exception
            raise RebacConnectionError(f"ReBAC network error: {e}") from e

    def list_objects(self, user: str, relation: str, object_type: str) -> list[str]:
        """Returns a list of object IDs the user has the specified relation to.

        Note: OpenFGA returns objects in the format 'type:id'. This method strips
        the 'type:' prefix so the core Django framework can directly use the raw
        IDs in `QuerySet.filter(id__in=...)`.

        Args:
            user: The subject string (e.g., 'user:123').
            relation: The action or role (e.g., 'viewer').
            object_type: The target resource type (e.g., 'document').

        Returns:
            List[str]: A list of raw Django primary keys.
        """
        try:
            response = self.client.list_objects(
                ClientListObjectsRequest(
                    user=user,
                    relation=relation,
                    type=object_type,
                ),
                options=self._read_options,
            )
        except ValidationException as e:
            logger.error(f"ReBAC ListObjects Schema Mismatch: {e}")
            return []
        except Exception as e:
            logger.error(f"ReBAC ListObjects network error: {e}")
            # Depending on how strict you want to be, you can return [] or raise
            # RebacConnectionError here
            raise RebacConnectionError(f"ReBAC network error: {e}") from e

        # OpenFGA returns "document:123". We strictly return "123" for the ORM.
        # `removeprefix` strips exactly one leading occurrence — `.replace()` would
        # rewrite every occurrence and mangle IDs that contain the type name.
        prefix = f"{object_type}:"
        return [obj.removeprefix(prefix) for obj in response.objects]

    def _convert_to_client_tuples(self, tuples: list[dict[str, str]]) -> list[ClientTuple]:
        """Helper to convert generic dictionary tuples to OpenFGA ClientTuples."""
        return [
            ClientTuple(
                user=t["user"],
                relation=t["relation"],
                object=t["object"],
            )
            for t in tuples
        ]

    def write_tuples(self, tuples: list[dict[str, str]]) -> None:
        """Writes relationships to the OpenFGA store.

        Args:
            tuples: A list of dictionaries containing 'user', 'relation', and 'object'.
        """
        if not tuples:
            return

        fga_writes = self._convert_to_client_tuples(tuples)
        request = ClientWriteRequest(writes=fga_writes, deletes=[])

        # We allow exceptions to bubble up here so the Celery task (ReBACSyncOutbox)
        # can catch them and trigger its retry logic automatically.
        # T2.6: conflict options make the write idempotent for safe outbox replays.
        self.client.write(request, options=_IDEMPOTENT_WRITE_OPTIONS)

    def delete_tuples(self, tuples: list[dict[str, str]]) -> None:
        """Deletes relationships from the OpenFGA store.

        Args:
            tuples: A list of dictionaries containing 'user', 'relation', and 'object'.
        """
        if not tuples:
            return

        fga_deletes = self._convert_to_client_tuples(tuples)
        request = ClientWriteRequest(writes=[], deletes=fga_deletes)

        # T2.6: ignore missing tuples so outbox replays stay idempotent.
        self.client.write(request, options=_IDEMPOTENT_WRITE_OPTIONS)

    def read_tuples(self, object: str) -> list[dict[str, str]]:
        """Returns the relationship tuples the store currently holds for one object.

        The read side of the sync contract — what makes the store verifiable.
        `rebac_reconcile` diffs this against the tuples Django state expects, which is
        what enables drift detection, orphan cleanup, and backfill verification.

        Args:
            object: The resource string (e.g., 'document:456').

        Returns:
            list[dict[str, str]]: The stored tuples for that object, each in the same
                {'user', 'relation', 'object'} shape accepted by `write_tuples`.

        Raises:
            RebacSchemaError: If the store rejects the object as invalid.
            RebacConnectionError: If the backend service is unreachable.
        """
        collected: list[dict[str, str]] = []
        continuation_token: str | None = None

        try:
            while True:
                page_options: dict[str, str] = dict(self._read_options or {})
                if continuation_token:
                    page_options["continuation_token"] = continuation_token

                response = self.client.read(
                    ReadRequestTupleKey(object=object),
                    options=page_options or None,
                )

                for t in response.tuples:
                    collected.append(
                        {"user": t.key.user, "relation": t.key.relation, "object": t.key.object}
                    )

                continuation_token = response.continuation_token
                if not continuation_token:
                    break
        except ValidationException as e:
            logger.error(f"ReBAC ReadTuples Schema Mismatch: {e}")
            raise RebacSchemaError(f"ReBAC Schema Mismatch reading {object}: {e}") from e
        except Exception as e:
            logger.error(f"ReBAC ReadTuples network error: {e}")
            raise RebacConnectionError(f"ReBAC network error: {e}") from e

        return collected

    def batch_check(self, checks: list[dict[str, str]]) -> dict[str, dict[str, bool]]:
        """
        Translates agnostic batch checks into OpenFGA SDK BatchRequests.

        Raises:
            RebacSchemaError: If the store rejects a check (unknown relation/type).
            RebacConnectionError: If the backend service is unreachable.

        Failures are propagated, never swallowed into a partial or empty map:
        callers (serializer mixin, batcher) catch `RebacError` and decide the
        response strategy. Returning {} would make "no permission"
        indistinguishable from "authorization backend is down".
        """
        if not checks:
            return {}

        fga_checks = [
            ClientBatchCheckItem(
                user=check["user"], relation=check["relation"], object=check["object"]
            )
            for check in checks
        ]

        batch_request = ClientBatchCheckRequest(checks=fga_checks)
        results_map: dict[str, dict[str, bool]] = {}

        try:
            batch_response = self.client.batch_check(batch_request, options=self._read_options)

            # OpenFGA returns results in the same order as the submitted checks
            # (the SDK itself relies on that ordering to pair results with requests).
            # Pair positionally so we only ever read the public `allowed` attribute
            # — no private SDK internals.
            for check, resp in zip(checks, batch_response.result, strict=False):
                results_map.setdefault(check["object"], {})[check["relation"]] = resp.allowed

        except ValidationException as e:
            logger.error(f"ReBAC Batch Check Schema Mismatch: {e}")
            raise RebacSchemaError(f"ReBAC Schema Mismatch in batch check: {e}") from e
        except Exception as e:
            logger.error(f"ReBAC Batch Check network error: {e}")
            raise RebacConnectionError(f"ReBAC network error: {e}") from e

        return results_map
