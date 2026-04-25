# rebac/serializers.py
from openfga_sdk.client.models import (
    ClientBatchCheckItem,
    ClientBatchCheckRequest,
)
from rest_framework import serializers

from ..conf import get_setting
from ..loggers import RebacConsoleLogger
from ..utils import get_rebac_client

logger = RebacConsoleLogger(__name__)


class RebacPermissionSerializerMixin(serializers.Serializer):
    """
    Package mixin for DRF Serializers.
    Reads from the OpenFGA batch map for lists, or runs a mini-batch for details.
    """

    _permissions = serializers.SerializerMethodField()

    @classmethod
    def many_init(cls, *args, **kwargs):
        """Forces DRF to use our batching list serializer for many=True."""
        kwargs["child"] = cls()
        from .batchers import RebacBatchListSerializer  # Import your batch serializer

        return RebacBatchListSerializer(*args, **kwargs)

    def get_field_names(self, declared_fields, info):
        """
        Overrides DRF to automatically append '_permissions' to the Meta.fields list
        if the developer configured rebac_permissions.
        """
        names = super().get_field_names(declared_fields, info)

        # If Rebac is configured for this serializer, force the field into the output
        if getattr(self.Meta, "rebac_permissions", None) and "_permissions" not in names:
            names = list(names)  # Ensure it is a mutable list
            names.append("_permissions")

        return names

    def get__permissions(self, obj) -> dict:
        rebac_object_type = getattr(self.Meta, "rebac_object_type", None)
        rebac_permissions = getattr(self.Meta, "rebac_permissions", [])

        if not rebac_object_type or not rebac_permissions:
            return {}

        object_key = f"{rebac_object_type}:{obj.pk}"

        # 1. LIST VIEW PATH: Try to read from the cached Batch Map
        batch_map = self.context.get("rebac_permissions_map")
        if batch_map is not None:
            return batch_map.get(object_key, {perm: False for perm in rebac_permissions})

        # 2. DETAIL VIEW PATH: Fallback for single item fetches
        request = self.context.get("request")
        if not request:
            return {perm: False for perm in rebac_permissions}

        user_attr = get_setting("REBAC_USER_ATTR")
        rebac_user = getattr(request, user_attr, None)

        if not rebac_user:
            return {perm: False for perm in rebac_permissions}

        rebac_client = get_rebac_client()
        results = {}

        try:
            # Run a mini-batch check for the single object's multiple permissions
            checks = [
                ClientBatchCheckItem(user=rebac_user, relation=p, object=object_key)
                for p in rebac_permissions
            ]
            batch_request = ClientBatchCheckRequest(checks=checks)
            batch_response = rebac_client.batch_check(batch_request)

            for resp in batch_response.responses:
                req = getattr(resp, "_request", getattr(resp, "request", None))
                if req:
                    results[req.relation] = resp.allowed

            return results
        except Exception as e:
            logger.error(f"Serializer Single Check Failed: {e}")
            return {perm: False for perm in rebac_permissions}
