# django_rebac/backends/openfga/defaults.py
from typing import Any

# These are the default configurations specifically required by the OpenFGA engine
BACKEND_DEFAULTS: dict[str, Any] = {
    "API_URL": "http://localhost:8080",
    "STORE_ID": None,
    # T3.10: Pin the authorization model (a ULID) so checks/list_objects evaluate
    # against a KNOWN schema. Leave as None to let OpenFGA use the store's LATEST
    # model — safe only while you never change the DSL. The standard safe migration
    # is: pin the current model -> write the new one -> verify against it -> flip pin.
    "AUTHORIZATION_MODEL_ID": None,
    # T3.10: Optional OpenFGA read consistency preference. Leave as None to keep the
    # OpenFGA server default (HIGHER_CONSISTENCY for check/list_objects). Set to
    # "MINIMAL_CONSISTENCY" to trade freshness for latency on hot list endpoints.
    "CONSISTENCY": None,
    # T3.10: HTTP request timeout in milliseconds forwarded to the SDK. Leave as None
    # to keep the SDK default (no timeout) — but a hung store then holds its connection
    # indefinitely, so set this in production (e.g., 5000).
    "TIMEOUT_MILLISEC": None,
    # T3.10: Path to a CA bundle (PEM) for private CA / mTLS deployments.
    "SSL_CA_CERT": None,
    # T3.10: Authentication for FGA managed service, Auth0 FGA, or secured self-host.
    # None (default) = no auth (unauthenticated localhost). Set to a dict:
    #   {"method": "api_token", "api_token": "..."} or
    #   {"method": "client_credentials", "client_id": "...", "client_secret": "...",
    #    "api_issuer": "https://auth.example.com", "api_audience": "optional",
    #    "scopes": "optional"}
    "CREDENTIALS": None,
    # T3.10: SDK-level retry of transient 429/5xx failures (dict of
    # {"max_retry": int, "min_wait_in_ms": int, "max_wait_in_sec": int}). None =
    # SDK default RetryParams(max_retry=3, min_wait_in_ms=100, max_wait_in_sec=120).
    "RETRY_PARAMS": None,
}
