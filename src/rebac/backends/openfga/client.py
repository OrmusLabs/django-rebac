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
    """

    def setup(self) -> None:
        """Instantiates the OpenFGA SDK Client.

        Raises:
            ReBACConfigurationError: If the `STORE_ID` is missing, or the client fails
                to initialize.
        """
        api_url: str = self.options.get("API_URL", "http://localhost:8080")
        store_id: str | None = self.options.get("STORE_ID")

        if not store_id:
            raise OpenFGAConfigurationError(
                "OpenFGABackend requires a 'STORE_ID' in REBAC['BACKEND_OPTIONS']."
            )

        config = ClientConfiguration(api_url=api_url, store_id=store_id)

        try:
            self.client = OpenFgaClient(config)
        except (ValueError, TypeError) as e:
            raise OpenFGAConfigurationError(f"Failed to initialize OpenFGA client: {e}") from e

        # T1.4: Optional OpenFGA read consistency preference. `None` keeps the OpenFGA
        # server default (HIGHER_CONSISTENCY for check/list_objects); set
        # "MINIMAL_CONSISTENCY" in BACKEND_OPTIONS to trade freshness for latency on
        # high-traffic list endpoints.
        consistency = self.options.get("CONSISTENCY")
        self._read_options: dict[str, str] | None = (
            {"consistency": consistency} if consistency else None
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
        prefix = f"{object_type}:"
        return [obj.replace(prefix, "") for obj in response.objects]

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

            for resp in batch_response.result:
                # Safely read from the SDK response object
                req = getattr(resp, "_request", getattr(resp, "request", None))
                if not req:
                    continue

                obj_key = req.object
                rel = req.relation

                if obj_key not in results_map:
                    results_map[obj_key] = {}
                results_map[obj_key][rel] = resp.allowed

        except Exception as e:
            logger.error(f"OpenFGA Batch Check Failed: {e}")

        return results_map
