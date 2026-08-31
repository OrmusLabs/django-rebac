# Settings & Utilities

This section covers the core configuration logic and the infrastructure utilities that power the `django-rebac` package under the hood.

---

## Configuration (`conf.py`)

The configuration module is responsible for parsing the `REBAC_CONFIG` dictionary defined in your core Django `settings.py` and falling back to sensible defaults.

::: rebac.conf
    options:
      show_root_heading: false
      heading_level: 3

---

## ReBAC Client Utility (`backends/openfga/client.py`)

The `get_rebac_client` function is the centralized infrastructure utility responsible for instantiating and configuring the ReBAC backend Python SDK client.

By utilizing this utility, we adhere to the **Single Responsibility Principle (SRP)**. Our application services and permission classes do not need to know *how* to authenticate with the ReBAC engine or *where* the ReBAC server lives; they simply request a ready-to-use client and execute their checks.

### ⚙️ How it works

Under the hood, `get_rebac_client()` fetches the necessary environment configuration from your Django `settings.py` (specifically the `REBAC_CONFIG` dictionary). It ensures that parameters like the `API_URL` and `STORE_ID` are properly loaded for OpenFGA Backend.

**Performance Note:** It utilizes Python's `@lru_cache` to act as a thread-safe **Singleton**, ensuring the underlying HTTP connection pool is reused across requests for maximum performance.

### 🔐 Production OpenFGA options (T3.10)

Everything the OpenFGA SDK needs for a real deployment is plumbed from `BACKEND_OPTIONS`. All of these default to `None` — unset options keep the SDK's own defaults, so an unauthenticated `localhost:8080` keeps working unchanged.

```python
REBAC_CONFIG = {
    "BACKEND_OPTIONS": {
        "API_URL": "https://your-fga.example.com",
        "STORE_ID": os.environ["FGA_STORE_ID"],

        # Authentication — required for FGA managed service, Auth0 FGA, or any
        # secured self-hosted deployment:
        "CREDENTIALS": {
            # Option A: static API token
            "method": "api_token",
            "api_token": os.environ["FGA_API_TOKEN"],
            # Option B: OAuth2 client credentials
            # "method": "client_credentials",
            # "client_id": "...", "client_secret": "...",
            # "api_issuer": "https://auth.example.com",
            # "api_audience": "...", "scopes": "openid profile",
        },

        # Pin the authorization model (ULID) so checks evaluate against a KNOWN
        # schema. Safe DSL migration = pin current → write new → verify → flip pin.
        "AUTHORIZATION_MODEL_ID": os.environ.get("FGA_MODEL_ID"),

        # HTTP timeout (ms). Without it a hung store holds its connection forever.
        "TIMEOUT_MILLISEC": 5000,

        # SDK-level retry of transient 429/5xx before the outbox retry loop.
        "RETRY_PARAMS": {"max_retry": 3, "min_wait_in_ms": 100, "max_wait_in_sec": 120},

        # Private CA bundle for mTLS / internal CA deployments.
        "SSL_CA_CERT": "/etc/ssl/private-ca.pem",
    },
}
```

Malformed `CREDENTIALS` (unknown method, missing parts) or `RETRY_PARAMS` raise `OpenFGAConfigurationError` at client initialization — a configuration mistake fails loudly at startup instead of per request.

::: rebac.utils
    options:
      show_root_heading: false
      heading_level: 3

### 🏗️ Architectural Usage Guidelines

To maintain our **Clean Architecture** and strict layer separation, follow these rules when using `get_rebac_client()` in your own application:

!!! warning "Rules of Engagement"
    * ❌ **DO NOT** use this client directly inside a Django Model (Layer 3). Models should only define ReBAC mappings declaratively using the `rebac_config` attribute with the `RebacModelConfig` data class.
    * ✅ **DO** use this client inside Custom Permissions (Layer 3) to protect your DRF API views.
    * ✅ **DO** use this client inside your Service Layer (Layer 2) if you need to manually query the authorization graph to make complex business logic decisions.

#### Example usage in a Service:

```python
# services.py
from rebac.utils import get_rebac_client

class DocumentService:
    def publish_document(self, document_id: str, user_id: str):
        # 1. Fetch the configured (and cached) agnostic ReBAC client
        rebac_client = get_rebac_client()

        # 2. Query ReBAC to ensure the user has the 'editor' role
        is_allowed = rebac_client.check(
            user=f"user:{user_id}",
            relation="editor",
            obj=f"document:{document_id}"
        )

        if not is_allowed:
            raise PermissionError("Only editors can publish this document.")

        # ... proceed with publishing business logic ...
```
