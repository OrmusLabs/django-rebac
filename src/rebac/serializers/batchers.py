# rebac/batchers.py
from openfga_sdk.client.models import ClientBatchCheckItem, ClientBatchCheckRequest
from rest_framework import serializers

from ..conf import get_setting
from ..loggers import RebacConsoleLogger
from ..utils import get_rebac_client

logger = RebacConsoleLogger(__name__)


class RebacBatchListSerializer(serializers.ListSerializer):
    """
    Intercepts list serialization to perform a single OpenFGA BatchCheck.
    Prevents N+1 network requests during collection views.
    """

    def to_representation(self, data):
        request = self.context.get("request")
        if not request:
            return super().to_representation(data)

        # 1. Resolve Identity via package settings
        user_attr = get_setting("REBAC_USER_ATTR")
        rebac_user = getattr(request, user_attr, None)

        if not rebac_user:
            return super().to_representation(data)

        iterable = data.all() if hasattr(data, "all") else list(data)
        if not iterable:
            return super().to_representation(data)

        # 2. Extract configuration from child's Meta class
        rebac_object_type = getattr(self.child.Meta, "rebac_object_type", None)
        rebac_permissions = getattr(self.child.Meta, "rebac_permissions", [])

        if not rebac_object_type or not rebac_permissions:
            return super().to_representation(data)

        # 3. Build the SDK Batch Checks
        checks = []
        for obj in iterable:
            # Use .pk to support both integer and UUID primary keys
            object_key = f"{rebac_object_type}:{obj.pk}"
            for perm in rebac_permissions:
                checks.append(
                    ClientBatchCheckItem(
                        user=rebac_user,
                        relation=perm,
                        object=object_key,
                    )
                )

        # 4. Execute via the cached package client
        rebac_client = get_rebac_client()
        rebac_permissions_map = {}

        try:
            batch_request = ClientBatchCheckRequest(checks=checks)
            batch_response = rebac_client.batch_check(batch_request)

            # Map the SDK responses into a fast dictionary lookup
            for resp in batch_response.responses:
                # Use getattr to safely read from the SDK response object
                req = getattr(resp, "_request", getattr(resp, "request", None))
                if not req:
                    continue

                obj_key = req.object
                rel = req.relation

                if obj_key not in rebac_permissions_map:
                    rebac_permissions_map[obj_key] = {}
                rebac_permissions_map[obj_key][rel] = resp.allowed

            self.context["rebac_permissions_map"] = rebac_permissions_map

        except Exception as e:
            logger.error(f"Serializer Batch Check Failed: {e}")
            self.context["rebac_permissions_map"] = {}

        return super().to_representation(data)
