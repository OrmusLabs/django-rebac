# django_rebac/backends/openfga/defaults.py
from typing import Any

# These are the default configurations specifically required by the OpenFGA engine
BACKEND_DEFAULTS: dict[str, Any] = {
    "API_URL": "http://localhost:8080",
    "STORE_ID": None,
    "AUTHORIZATION_MODEL_ID": None,
    # T1.4: Optional OpenFGA read consistency preference. Leave as None to keep the
    # OpenFGA server default (HIGHER_CONSISTENCY for check/list_objects). Set to
    # "MINIMAL_CONSISTENCY" to trade freshness for latency on hot list endpoints.
    "CONSISTENCY": None,
}
