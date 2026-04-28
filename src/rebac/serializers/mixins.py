# rebac/serializers.py
from rest_framework import serializers

from ..backends.base.exceptions import RebacError
from ..common.loggers import RebacConsoleLogger
from ..conf import get_setting
from ..utils import get_rebac_client

logger = RebacConsoleLogger(__name__)


class RebacPermissionSerializerMixin(serializers.Serializer):
    """
    Package mixin for DRF Serializers.
    Reads from the ReBAC batch map for lists, or runs a mini-batch for details.
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
        batch_map = self.context.get("rebac_permissions_map")

        if batch_map is not None:
            return batch_map.get(object_key, {perm: False for perm in rebac_permissions})

        request = self.context.get("request")
        if not request:
            return {perm: False for perm in rebac_permissions}

        user_attr = get_setting("REBAC_USER_ATTR")
        rebac_user = getattr(request, user_attr, None)

        if not rebac_user:
            return {perm: False for perm in rebac_permissions}

        # Build pure agnostic checks for the single detail object
        checks = [
            {"user": rebac_user, "relation": p, "object": object_key} for p in rebac_permissions
        ]

        rebac_client = get_rebac_client()
        try:
            results_map = rebac_client.batch_check(checks)
        except RebacError as e:
            logger.error(f"ReBAC batch check failed: {e}")
            results_map = {}

        # Safe extraction from the map
        return results_map.get(object_key, {perm: False for perm in rebac_permissions})
