# rebac/batchers.py
from rest_framework import serializers

from ..backends.base.exceptions import RebacError
from ..conf import get_setting
from ..loggers import RebacConsoleLogger
from ..utils import get_rebac_client

logger = RebacConsoleLogger(__name__)


class RebacBatchListSerializer(serializers.ListSerializer):
    """
    Intercepts list serialization to perform a single ReBAC batch check.
    Prevents N+1 network requests during collection views.
    """

    def to_representation(self, data):
        request = self.context.get("request")
        if not request:
            return super().to_representation(data)

        user_attr = get_setting("REBAC_USER_ATTR")
        rebac_user = getattr(request, user_attr, None)

        if not rebac_user:
            return super().to_representation(data)

        iterable = data.all() if hasattr(data, "all") else list(data)
        if not iterable:
            return super().to_representation(data)

        rebac_object_type = getattr(self.child.Meta, "rebac_object_type", None)
        rebac_permissions = getattr(self.child.Meta, "rebac_permissions", [])

        if not rebac_object_type or not rebac_permissions:
            return super().to_representation(data)

        # Build pure agnostic check dictionaries
        checks = []
        for obj in iterable:
            object_key = f"{rebac_object_type}:{obj.pk}"
            for perm in rebac_permissions:
                checks.append({"user": rebac_user, "relation": perm, "object": object_key})

        # Execute via the abstract interface
        rebac_client = get_rebac_client()
        try:
            self.context["rebac_permissions_map"] = rebac_client.batch_check(checks)
        except RebacError as e:
            logger.error(f"ReBAC batch list check failed: {e}")
            self.context["rebac_permissions_map"] = {}

        return super().to_representation(data)
