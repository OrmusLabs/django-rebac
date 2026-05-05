from typing import Any

from django.core.signals import setting_changed
from django.dispatch import receiver

from .utils import get_rebac_client


@receiver(setting_changed)
def _clear_fga_client_cache(sender: Any, setting: str, **kwargs: Any) -> None:  # pragma: no cover
    """
    Automatically clears the lru_cache when Django settings are overridden in tests.
    """
    if setting == "REBAC_CONFIG":
        get_rebac_client.cache_clear()
