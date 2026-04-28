# rebac/views/mixins.py
import logging
from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.request import Request
from rest_framework.viewsets import ViewSetMixin

from rebac.backends.base.exceptions import RebacError

from ..conf import get_setting
from ..loggers import RebacConsoleLogger
from ..structs import RebacViewConfig
from ..utils import get_rebac_client

__all__ = [
    "RebacViewMixin",
]

logger = logging.getLogger(__name__)

dev_logger = RebacConsoleLogger(__name__)


class RebacViewMixin:
    """Structure-agnostic mixin for DRF Views to enforce ReBAC Authorization.

    This mixin automatically handles three critical authorization lifecycle hooks
    in Django REST Framework without requiring manual permission logic:

    1. Queryset Filtering (`get_queryset`): Injects `id__in` filters on list views.
    2. Parent Verification (`check_permissions`): Checks required parent roles on POST requests.
    3. Object Verification (`check_object_permissions`): Checks specific object roles on
       PUT/PATCH/DELETE.

    Attributes:
        rebac_config: The strict configuration class defining the authorization rules
                    for this view. Must be an instance of `RebacViewConfig`.

    Example:
        ```python
        from rest_framework import viewsets
        from rebac_data_sync.structs import RebacViewConfig

        class DocumentViewSet(RebacViewMixin, viewsets.ModelViewSet):
            queryset = Document.objects.all()
            serializer_class = DocumentSerializer

            rebac_config = RebacViewConfig(
                object_type="document",
                read_relation="can_read_document",
                update_relation="can_update",
                delete_relation="can_delete"
            )
        ```

    Raises:
        ImproperlyConfigured: If `rebac_config` is missing, invalid, or utilizes
                              ViewSet-only features (like `action_relations`) on
                              a standard Generic View.
        AuthenticationFailed: If the Traefik identity header is missing.
        PermissionDenied: If the OpenFGA network check denies access.
    """

    rebac_config: ClassVar[RebacViewConfig | None] = None

    request: Request
    kwargs: dict[str, Any]
    lookup_field: str

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        # 🛡️ THE GUARDRAIL: Check for duplicate authorization strategies
        from ..permissions import IsRebacAuthorized

        permission_classes = getattr(self, "permission_classes", [])
        if IsRebacAuthorized in permission_classes:
            # Warning: The developer applied redundant permission checks!
            dev_logger.warning(
                f"Duplicate ReBAC Authorization detected on '{self.__class__.__name__}'. "
                f"You are using both 'RebacViewMixin' and 'IsRebacAuthorized'. "
                f"This will result in redundant network calls. "
                f"Please remove 'IsRebacAuthorized' from 'permission_classes'."
            )

    def _get_rebac_user(self) -> str:
        user_attr = get_setting("REBAC_USER_ATTR")
        rebac_user = getattr(self.request, user_attr, None)
        if not rebac_user:
            raise AuthenticationFailed(
                f"Missing identity context on request attribute '{user_attr}'."
            )
        return rebac_user

    def _get_config(self) -> RebacViewConfig:
        if not isinstance(self.rebac_config, RebacViewConfig):
            raise ImproperlyConfigured("View must define `rebac_config` of type `RebacViewConfig`.")

        if self.rebac_config.action_relations and not isinstance(self, ViewSetMixin):
            raise ImproperlyConfigured(
                f"View '{self.__class__.__name__}' defines 'action_relations' in its "
                f"RebacViewConfig, but it is not a ViewSet. Standard Generic Views "
                "do not support @action decorators."
            )

        return self.rebac_config

    def get_serializer_context(self) -> dict[str, Any]:
        """DRF hook: Injects ReBAC tools directly into the serializer context."""
        context = super().get_serializer_context()  # type: ignore[misc]

        user_attr = get_setting("REBAC_USER_ATTR")
        rebac_user = getattr(self.request, user_attr, None)

        if rebac_user:
            context["rebac_user"] = rebac_user
            context["rebac_client"] = get_rebac_client()

        return context

    def get_queryset(self) -> Any:
        queryset = super().get_queryset()  # type: ignore[misc]
        view_kwargs = getattr(self, "kwargs", {})

        # 1. Bypass if this is a Detail request (e.g., /api/companies/1/)
        if view_kwargs.get(self.lookup_field):
            return queryset

        config = self._get_config()

        # 2. Explicitly bypass ReBAC filtering if requested
        if config.disable_list_filter:
            return queryset

        relation_to_check = config.list_relation or config.read_relation

        # 3. Perform the abstract Backend network check
        if config.object_type and relation_to_check:
            # 🛠️ THE FIX: Extract user before the try/except block
            rebac_user = self._get_rebac_user()
            client = get_rebac_client()
            try:
                allowed_ids: list[str] = client.list_objects(
                    user=rebac_user,
                    relation=relation_to_check,
                    object_type=config.object_type,
                )
            except RebacError as e:
                error_msg = f"ReBAC backend validation failed: {e}"
                logger.error(error_msg)
                raise ImproperlyConfigured(error_msg) from e

            return queryset.filter(id__in=allowed_ids)

        return queryset

    def check_permissions(self, request: Request) -> None:
        super().check_permissions(request)  # type: ignore[misc]
        config = self._get_config()

        if request.method == "POST" and config.create_scope_type:
            parent_field = str(config.create_scope_field)
            parent_id: str | None = request.data.get(parent_field)

            # Model Instantiation Fallback for resolving payload data
            if not parent_id:
                try:
                    model_class = None
                    view_queryset = getattr(self, "queryset", None)
                    get_qs_func = getattr(self, "get_queryset", None)

                    if view_queryset is not None:
                        model_class = view_queryset.model
                    elif callable(get_qs_func):
                        model_class = get_qs_func().model

                    if model_class:
                        dummy_instance = model_class()
                        parent_id = getattr(dummy_instance, parent_field, None)
                except Exception as e:  # pragma: no cover
                    logger.debug(f"Failed to resolve {parent_field} from model fallback: {e}")

            if not parent_id:
                raise PermissionDenied(f"Payload must include parent field: '{parent_field}'")

            if config.create_relation is None:  # pragma: no cover
                raise ImproperlyConfigured("RebacViewConfig must define `create_relation`.")

            rebac_user = self._get_rebac_user()
            client = get_rebac_client()
            try:
                is_allowed = client.check(
                    user=rebac_user,
                    relation=config.create_relation,
                    obj=f"{config.create_scope_type}:{parent_id}",
                )
            except RebacError as e:
                error_msg = f"ReBAC backend execution error: {e}"
                logger.error(error_msg)
                raise ImproperlyConfigured(error_msg) from e

            if not is_allowed:
                raise PermissionDenied(
                    f"You must be '{config.create_relation}' on '{config.create_scope_type}' "
                    "to create this object."
                )
            return

        if config.lookup_header or config.lookup_url_kwarg:
            self.check_object_permissions(request, obj=None)

    def check_object_permissions(self, request: Request, obj: Any) -> None:
        super().check_object_permissions(request, obj)  # type: ignore[misc]
        config = self._get_config()

        relation: str | None = None
        view_action: str | None = getattr(self, "action", None)

        if view_action:
            relation = config.action_relations.get(view_action)

        if not relation:
            if request.method in ["PUT", "PATCH"]:
                relation = config.update_relation
            elif request.method == "DELETE":
                relation = config.delete_relation
            elif request.method in ["GET", "OPTIONS", "HEAD"]:
                relation = config.read_relation

        if config.object_type and relation:
            object_id = None
            if config.lookup_header:
                object_id = request.META.get(config.lookup_header)
            elif config.lookup_url_kwarg:
                object_id = getattr(self, "kwargs", {}).get(config.lookup_url_kwarg)
            else:
                object_id = getattr(obj, "pk", None)

            if not object_id:
                raise PermissionDenied("Target object identifier could not be resolved.")

            rebac_user = self._get_rebac_user()
            client = get_rebac_client()
            try:
                is_allowed = client.check(
                    user=rebac_user,
                    relation=relation,
                    obj=f"{config.object_type}:{object_id}",
                )
            except RebacError as e:
                error_msg = f"ReBAC backend execution error: {e}"
                logger.error(error_msg)
                raise ImproperlyConfigured(error_msg) from e

            if not is_allowed:
                raise PermissionDenied(f"You do not have '{relation}' access to this object.")
