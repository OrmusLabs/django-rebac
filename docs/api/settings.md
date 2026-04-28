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
from openfga_sdk.client.models import ClientCheckRequest
from rebac.utils import get_rebac_client

class DocumentService:
    def publish_document(self, document_id: str, user_id: str):
        # 1. Fetch the configured (and cached) ReBAC client
        rebac_client = get_rebac_client()

        # 2. Query ReBAC to ensure the user has the 'editor' role
        response = rebac_client.check(
            ClientCheckRequest(
                user=f"user:{user_id}",
                relation="editor",
                object=f"document:{document_id}",
            )
        )

        if not response.allowed:
            raise PermissionError("Only editors can publish this document.")

        # ... proceed with publishing business logic ...
```
