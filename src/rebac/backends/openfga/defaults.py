# django_rebac/backends/openfga/defaults.py
from typing import Any

# These are the default configurations specifically required by the OpenFGA engine
BACKEND_DEFAULTS: dict[str, Any] = {
    "API_URL": "http://localhost:8080",
    "STORE_ID": None,
    "AUTHORIZATION_MODEL_ID": None,
}
