# rebac/batchers.py
from rest_framework import serializers

from ..backends.base.exceptions import RebacError
from ..common.loggers import RebacConsoleLogger
from ..conf import get_setting
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

        # Materialize the queryset exactly once. (Previously `data.all()` cloned
        # the queryset and `super()` re-iterated the original — two full fetches.)
        iterable = list(data)
        if not iterable:
            return super().to_representation(iterable)

        rebac_object_type = getattr(self.child.Meta, "rebac_object_type", None)
        rebac_permissions = getattr(self.child.Meta, "rebac_permissions", [])

        if not rebac_object_type or not rebac_permissions:
            return super().to_representation(iterable)

        # Build pure agnostic check dictionaries
        checks = []
        for obj in iterable:
            object_key = f"{rebac_object_type}:{obj.pk}"
            for perm in rebac_permissions:
                checks.append({"user": rebac_user, "relation": perm, "object": object_key})

        # Execute via the abstract interface.
        # The context key is namespaced per object type: DRF shares one context
        # dict across the whole serializer tree, so an unnamespaced key would let
        # a nested list of a different type clobber this one's permissions map.
        rebac_client = get_rebac_client()
        map_key = f"rebac_permissions_map::{rebac_object_type}"
        try:
            self.context[map_key] = rebac_client.batch_check(checks)
        except RebacError as e:
            logger.error(f"ReBAC batch list check failed: {e}")
            self.context[map_key] = {}

        return super().to_representation(iterable)
