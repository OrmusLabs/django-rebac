import logging
from typing import Any, Protocol

from django.core.exceptions import ImproperlyConfigured
from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSetMixin

from rebac.backends.base.exceptions import RebacError

from .conf import get_setting
from .loggers import RebacConsoleLogger
from .structs import RebacViewConfig
from .utils import get_rebac_client

logger = logging.getLogger(__name__)

dev_logger = RebacConsoleLogger(__name__)


class RebacConfiguredView(Protocol):
    """Protocol defining the expected interface for an ReBAC-protected view."""

    rebac_config: RebacViewConfig
    action: str | None = None
    kwargs: dict[str, Any]


UPDATE_METHODS: set[str] = {"PUT", "PATCH"}
DELETE_METHOD: str = "DELETE"


class IsRebacAuthorized(permissions.BasePermission):
    """Evaluates ReBAC authorization rules strictly based on the view's RebacViewConfig."""

    def _get_config(self, view: APIView | RebacConfiguredView) -> RebacViewConfig:
        """Safely extracts and validates the RebacViewConfig from the view.

        Args:
            view: The DRF view instance being accessed.

        Returns:
            RebacViewConfig: The validated configuration object.

        Raises:
            ImproperlyConfigured: If the view lacks a valid rebac_config.
        """
        config = getattr(view, "rebac_config", None)
        if not isinstance(config, RebacViewConfig):
            raise ImproperlyConfigured(
                f"View '{view.__class__.__name__}' using IsRebacAuthorized must "
                f"define an `rebac_config` attribute of type `RebacViewConfig`."
            )

        if config.action_relations and not isinstance(view, ViewSetMixin):
            raise ImproperlyConfigured(
                f"View '{view.__class__.__name__}' defines 'action_relations' in its "
                f"RebacViewConfig, but it is not a ViewSet. Standard Generic Views do not support "
                f"@action decorators. Either convert this view to a ViewSet, or remove "
                f"'action_relations' from the config."
            )

        if config.list_relation or config.disable_list_filter:
            has_mixin = any(cls.__name__ == "RebacViewMixin" for cls in view.__class__.__mro__)

            if not has_mixin:
                # Warning: The developer forgot to add the mixin!
                dev_logger.warning(
                    f"View '{view.__class__.__name__}' uses 'list_relation' or "
                    f"'disable_list_filter' in its RebacViewConfig, but does not inherit "
                    f"from 'RebacViewMixin'. The 'IsRebacAuthorized' permission class "
                    f"cannot filter lists and will safely ignore these settings."
                )

        return config

    def has_permission(self, request: Request, view: APIView | RebacConfiguredView) -> bool:
        """Validates identity context and handles creation (POST) parent verification.

        Args:
            request: The incoming HTTP request.
            view: The DRF view instance.

        Returns:
            bool: True if the request is allowed to proceed, False otherwise.
        """
        user_attr = get_setting("REBAC_USER_ATTR")
        rebac_user: str | None = getattr(request, user_attr, None)

        if not rebac_user:
            logger.warning(f"ReBAC Authorization denied: No '{user_attr}' found on request.")
            return False

        config = self._get_config(view)

        # Handle POST (Creation) Parent Checks
        if request.method == "POST" and config.create_scope_type:
            # Type hint resolution: __post_init__ guarantees these are strings if one exists
            parent_field = str(config.create_scope_field)
            parent_id: str | None = request.data.get(parent_field)

            # Model Instantiation Fallback
            if not parent_id:
                try:
                    model_class = None

                    # 1. Safely extract the attribute to a local variable to satisfy MyPy
                    view_queryset = getattr(view, "queryset", None)
                    get_qs_func = getattr(view, "get_queryset", None)

                    if view_queryset is not None:
                        model_class = view_queryset.model
                    elif callable(get_qs_func):
                        model_class = get_qs_func().model

                    # 3. Instantiate an empty dummy instance and read the property
                    if model_class:
                        dummy_instance = model_class()
                        parent_id = getattr(dummy_instance, parent_field, None)
                except Exception as e:  # pragma: no cover
                    logger.debug(f"Failed to resolve {parent_field} from model fallback: {e}")

            if not parent_id:
                logger.warning(
                    f"ReBAC Authorization denied: Missing parent field '{parent_field}' in payload."
                )
                return False

            rebac_client = get_rebac_client()
            try:
                is_allowed = rebac_client.check(
                    user=rebac_user,
                    relation=str(config.create_relation),
                    obj=f"{config.create_scope_type}:{parent_id}",
                )
                return is_allowed
            except RebacError as e:
                logger.error(f"ReBAC backend error during parent check: {e}")
                return False

        if config.lookup_header or config.lookup_url_kwarg:
            return self.has_object_permission(request, view, obj=None)

        return True

    def has_object_permission(
        self, request: Request, view: APIView | RebacConfiguredView, obj: object
    ) -> bool:
        """Validates fine-grained object access based on HTTP method or ViewSet action.

        If the corresponding relation in the view's RebacViewConfig is explicitly set
        to None, this check is bypassed and access is automatically granted, adhering
        to the documented opt-out contract.

        Args:
            request: The incoming HTTP request.
            view: The DRF view instance.
            obj: The database object being accessed.

        Returns:
            bool: True if the user holds the required relation on the object (or if the
                  check is explicitly disabled), False otherwise.
        """
        config = self._get_config(view)
        required_relation: str | None = None
        is_relation_mapped: bool = False

        # 1. Resolve relation via Custom ViewSet Action
        view_action: str | None = getattr(view, "action", None)
        if view_action and view_action in config.action_relations:
            required_relation = config.action_relations.get(view_action)
            is_relation_mapped = True

        # 2. Resolve relation via Standard HTTP Methods
        if not is_relation_mapped:
            if request.method in permissions.SAFE_METHODS:
                required_relation = config.read_relation
                is_relation_mapped = True
            elif request.method in UPDATE_METHODS:
                required_relation = config.update_relation
                is_relation_mapped = True
            elif request.method == DELETE_METHOD:
                required_relation = config.delete_relation
                is_relation_mapped = True

        # If the HTTP method wasn't mapped at all (e.g., TRACE, CONNECT), deny by default.
        if not is_relation_mapped:
            logger.warning(f"ReBAC Authorization denied: Unmapped HTTP method '{request.method}'.")
            return False

        # 3. Handle Explicit Opt-Out (Relation is mapped, but explicitly set to None)
        if required_relation is None:
            return True

        # 4. Perform ReBAC Network Check
        user_attr = get_setting("REBAC_USER_ATTR")
        rebac_user: str | None = getattr(request, user_attr, None)
        if not rebac_user:
            logger.warning("ReBAC Authorization denied: No 'rebac_user' found on request.")
            return False

        try:
            # Defensive lookup for object identifier
            # 🤠 NEW: Resolve Object ID Statelessly OR Statefuly
            if config.lookup_header:
                object_id = request.META.get(config.lookup_header)
            elif config.lookup_url_kwarg:
                object_id = view.kwargs.get(config.lookup_url_kwarg)
            else:
                object_id = getattr(obj, "id", getattr(obj, "pk", None))

            if not object_id:
                logger.error("Authorization target lacks an identifier.")
                return False

            rebac_client = get_rebac_client()
            try:
                is_allowed = rebac_client.check(
                    user=rebac_user,
                    relation=required_relation,
                    obj=f"{config.object_type}:{object_id}",
                )
                return is_allowed
            except RebacError as e:
                logger.error(f"ReBAC backend error during object check: {e}")
                return False
        # except ValidationException as e:
        #     error_msg = (
        #         f"ReBAC DSL Mismatch: The relation '{required_relation}' on type "
        #         f"'{config.object_type}' does not exist in your OpenFGA schema. "
        #         f"Please update your DSL or fix your RebacViewConfig."
        #     )
        #     logger.error(error_msg)
        #     raise ImproperlyConfigured(error_msg) from e
        except Exception as e:
            logger.error(f"ReBAC network or validation error during object check: {e}")
            return False
