from functools import lru_cache

from app.config import get_settings
from app.services.discovery import DiscoveryService
from app.services.store import store


@lru_cache
def get_discovery_service() -> DiscoveryService:
    return DiscoveryService(get_settings(), store)
