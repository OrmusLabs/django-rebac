# rebac/middleware.py
import logging
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from .conf import get_setting

logger = logging.getLogger(__name__)


class GatewayIdentityMiddleware:
    """Extracts API Gateway headers and attaches the ReBAC subject string to the request.

    This middleware reads headers (like X-User-Id) from incoming requests (typically set
    by an API Gateway like Traefik, Kong, or Nginx) and formats them as a ReBAC subject
    string (e.g., "user:123"). These are then attached to the Django request object.

    Attributes:
        get_response: The next middleware or view in the chain.

    Example:
        >>> # In settings.py
        >>> MIDDLEWARE = [
        ...     "rebac.middleware.GatewayIdentityMiddleware",
        ...     ...
        ... ]
        >>> # In settings.py
        >>> REBAC = {
        ...     "REQUEST_HEADER_MAPPINGS": {
        ...         "X-User-Id": "rebac_user",
        ...         "X-Context-Org-Id": "active_tenant"
        ...     },
        ...     "REBAC_USER_ATTR": "rebac_user"
        ... }
        >>>
        >>> # In a view
        >>> def my_view(request):
        ...     user_id = request.rebac_user  # e.g., "user:abc123"
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Initializes the middleware.

        Args:
            get_response: The next middleware or view callable in the Django chain.
        """
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Processes the incoming request to attach ReBAC identity attributes.

        Args:
            request (HttpRequest): The incoming HTTP request from Django.

        Returns:
            HttpResponse: The generated response from the downstream view/middleware.
        """
        header_mappings: dict[str, str] = get_setting("REQUEST_HEADER_MAPPINGS")
        rebac_user_attr: str = get_setting("REBAC_USER_ATTR")
        rebac_prefix: str = get_setting("REBAC_USER_PREFIX")
        local_dev_config: dict[str, Any] = get_setting("LOCAL_DEV_FALLBACK")

        # Safely check debug status (defensive against unconfigured settings)
        is_debug: bool = getattr(settings, "DEBUG", False)

        for header_name, target_attr in header_mappings.items():
            # 1. Defensive Initialization: Guarantee the attribute exists on the request
            setattr(request, target_attr, None)

            # 2. Standard Gateway Extraction
            header_value = request.headers.get(header_name)

            # 3. Local Development Fallbacks (If Gateway header is missing)
            if not header_value and is_debug:
                # Fallback A: Use the logged-in Django Database User
                if (
                    local_dev_config.get("USE_DJANGO_USER")
                    and hasattr(request, "user")
                    and request.user.is_authenticated
                ):
                    header_value = str(request.user.id)
                    logger.debug("🛠️ Local Dev: Falling back to Django User -> %s", header_value)

                # Fallback B: Use a static string
                elif local_dev_config.get("STATIC_USER_ID"):
                    header_value = str(local_dev_config.get("STATIC_USER_ID"))
                    logger.debug("🛠️ Local Dev: Falling back to Static User -> %s", header_value)

            # 4. Apply prefix and attach if a value was resolved
            if header_value:
                if target_attr == rebac_user_attr:
                    header_value = f"{rebac_prefix}{header_value}"

                setattr(request, target_attr, header_value)

        return self.get_response(request)
