# django_rebac/backends/openfga/client.py
import logging

from openfga_sdk.client import ClientConfiguration
from openfga_sdk.client.models import (
    ClientBatchCheckItem,
    ClientBatchCheckRequest,
    ClientCheckRequest,
    ClientListObjectsRequest,
    ClientTuple,
    ClientWriteRequest,
)
from openfga_sdk.exceptions import ValidationException
from openfga_sdk.sync import OpenFgaClient

from rebac.backends.base.client import BaseReBACBackend
from rebac.backends.base.exceptions import RebacConnectionError, RebacSchemaError

from .exceptions import OpenFGAConfigurationError

logger = logging.getLogger(__name__)


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
                )
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
                )
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
        self.client.write(request)

    def delete_tuples(self, tuples: list[dict[str, str]]) -> None:
        """Deletes relationships from the OpenFGA store.

        Args:
            tuples: A list of dictionaries containing 'user', 'relation', and 'object'.
        """
        if not tuples:
            return

        fga_deletes = self._convert_to_client_tuples(tuples)
        request = ClientWriteRequest(writes=[], deletes=fga_deletes)

        self.client.write(request)

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
            batch_response = self.client.batch_check(batch_request)

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
