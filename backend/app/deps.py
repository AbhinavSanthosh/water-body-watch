from functools import lru_cache

from app.config import get_settings
from app.forensics.briefing import BriefingService
from app.forensics.service import ForensicService
from app.history.service import HistoryService
from app.live.service import LiveService
from app.services.discovery import DiscoveryService
from app.services.store import store


@lru_cache
def get_discovery_service() -> DiscoveryService:
    return DiscoveryService(get_settings(), store)


@lru_cache
def get_history_service() -> HistoryService:
    return HistoryService(get_settings(), store)


@lru_cache
def get_live_service() -> LiveService:
    return LiveService(get_settings(), store, history=get_history_service())


@lru_cache
def get_forensics_service() -> ForensicService:
    settings = get_settings()
    return ForensicService(
        settings,
        store,
        history=get_history_service(),
        live=get_live_service(),
    )


@lru_cache
def get_briefing_service() -> BriefingService:
    settings = get_settings()
    return BriefingService(settings, store, forensics=get_forensics_service())
