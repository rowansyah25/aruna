"""Data access objects.  One module per aggregate, raw SQL inside, typed
dataclasses out - nothing above this layer touches driver rows directly."""

from aruna.db.repositories.agents import DeliberationRepository
from aruna.db.repositories.analysis import AnalysisRepository
from aruna.db.repositories.app_state import AppStateRepository
from aruna.db.repositories.events import AuditRepository, SystemEventRepository
from aruna.db.repositories.fundamental import (
    CorrelationRepository,
    FundamentalRepository,
)
from aruna.db.repositories.market_data import MarketDataRepository
from aruna.db.repositories.news import NewsRepository
from aruna.db.repositories.telegram import TelegramSubscriberRepository
from aruna.db.repositories.universe import AssetRecord, MarketRecord, UniverseRepository

__all__ = [
    "AnalysisRepository",
    "AppStateRepository",
    "AssetRecord",
    "AuditRepository",
    "CorrelationRepository",
    "DeliberationRepository",
    "FundamentalRepository",
    "MarketDataRepository",
    "MarketRecord",
    "NewsRepository",
    "SystemEventRepository",
    "TelegramSubscriberRepository",
    "UniverseRepository",
]
