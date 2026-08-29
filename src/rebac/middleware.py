# rebac/middleware.py
import logging
import re
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

    @staticmethod
    def _is_from_trusted_proxy(request: HttpRequest, trusted_proxies: list[str]) -> bool:
        """T1.3: Verifies the request originated from one of the trusted proxies.

        Allowlist entries are matched exactly against the request's REMOTE_ADDR.
        Comma-separated hop lists (produced by some proxy chains) are checked
        hop-by-hop; a match on any hop counts as trusted.

        Args:
            request: The incoming HTTP request.
            trusted_proxies: The configured allowlist (e.g., ["10.0.0.5"]).

        Returns:
            bool: True when the gate is disabled (empty allowlist, the default)
            or when the request came from an allowed address. A missing
            REMOTE_ADDR is treated as untrusted whenever the gate is enabled.
        """
        if not trusted_proxies:
            return True
        allowlist = {str(entry) for entry in trusted_proxies}
        remote_addr = str(request.META.get("REMOTE_ADDR") or "")
        return any(hop.strip() in allowlist for hop in remote_addr.split(",") if hop.strip())

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
        trusted_proxies: list[str] = local_dev_config.get("TRUSTED_PROXIES", [])
        # T1.3: Trusted-proxy gate. Off by default (empty allowlist) for
        # backward compatibility; once configured, inbound identity/context
        # headers are only honored from the listed proxy addresses.
        from_trusted_proxy: bool = self._is_from_trusted_proxy(request, trusted_proxies)

        # Safely check debug status (defensive against unconfigured settings)
        is_debug: bool = getattr(settings, "DEBUG", False)

        for header_name, target_attr in header_mappings.items():
            # 1. Defensive Initialization: Guarantee the attribute exists on the request
            setattr(request, target_attr, None)

            # 2. Standard Gateway Extraction
            header_value = request.headers.get(header_name)

            # 2b. T1.3: Trusted-proxy gate — if an allowlist is configured and the
            # request did NOT come from one of the trusted proxies, the inbound
            # value is client-controlled and must be dropped. The attribute stays
            # None so the local-dev fallbacks below (DEBUG only) may still apply.
            if header_value and trusted_proxies and not from_trusted_proxy:
                logger.error(
                    "ReBAC: Dropping inbound header '%s' — request is not from a "
                    "trusted proxy (REMOTE_ADDR=%r, TRUSTED_PROXIES=%r). Route "
                    "traffic through a trusting-boundary proxy (e.g., Traefik "
                    "forward-auth) that sets this header, or add the proxy's IP "
                    "to LOCAL_DEV_FALLBACK['TRUSTED_PROXIES'].",
                    header_name,
                    request.META.get("REMOTE_ADDR"),
                    trusted_proxies,
                )
                header_value = None

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
            # T1.3: Validate header value if present (T1.3: Identity header validation)
            if header_value:
                # T1.3: Reject wildcard values like "user:*" which match all grants in OpenFGA
                if header_value == "*" or header_value.endswith(":*"):
                    logger.error(
                        f"ReBAC: Invalid wildcard user identifier detected: {header_value}"
                    )
                    header_value = None  # Treat as no header
                # Validate user identifier format (without prefix)
                elif target_attr == rebac_user_attr:
                    # Remove prefix if present to validate the raw identifier
                    raw_id = header_value
                    if rebac_prefix and header_value.startswith(rebac_prefix):
                        raw_id = header_value[len(rebac_prefix) :]
                    # Only allow alphanumeric, underscore, and hyphen in user IDs
                    if not re.match(r"^[a-zA-Z0-9_-]+$", raw_id):
                        logger.error(f"ReBAC: Invalid user identifier format '{header_value}'")
                        header_value = None

            if header_value:
                if target_attr == rebac_user_attr:
                    header_value = f"{rebac_prefix}{header_value}"

                setattr(request, target_attr, header_value)

        return self.get_response(request)
