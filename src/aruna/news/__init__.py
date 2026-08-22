"""News engine (SPEC 8).

Only the domain types and the provider are re-exported here.
:class:`~aruna.news.service.NewsService` is imported from its own module: it
depends on the repository layer, which in turn imports these models, so
exporting it here would close an import cycle.
"""

from aruna.news.models import Importance, NewsCategory, NewsItem, Sentiment
from aruna.news.rss import DEFAULT_FEEDS, Feed, RssNewsProvider

__all__ = [
    "DEFAULT_FEEDS",
    "Feed",
    "Importance",
    "NewsCategory",
    "NewsItem",
    "RssNewsProvider",
    "Sentiment",
]
