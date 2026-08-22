"""Shared fixtures.

Tests must not read the developer's ``.env``.  Every settings object built here
passes ``_env_file=None`` and the autouse fixture strips ``ARUNA_*`` from the
environment, so a test result never depends on local configuration.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from aruna.core.config import (
    AppSettings,
    DatabaseSettings,
    HealthSettings,
    LogSettings,
    ProviderSettings,
    RedisSettings,
    Settings,
    TelegramSettings,
    UpkeepSettings,
)
from aruna.core.logging import reset_logging


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("ARUNA_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    yield
    reset_logging()


def make_settings(**overrides: object) -> Settings:
    """A fully specified Settings object that ignores the environment."""
    groups: dict[str, object] = {
        "app": AppSettings(_env_file=None),
        "log": LogSettings(_env_file=None),
        "db": DatabaseSettings(_env_file=None),
        "redis": RedisSettings(_env_file=None),
        "telegram": TelegramSettings(_env_file=None),
        "health": HealthSettings(_env_file=None),
        "providers": ProviderSettings(_env_file=None),
        "upkeep": UpkeepSettings(_env_file=None),
    }
    groups.update(overrides)
    return Settings(**groups)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def telegram_settings() -> TelegramSettings:
    return TelegramSettings(
        _env_file=None,
        enabled=True,
        bot_token=SecretStr("123456789:AAFakeTokenForTestsOnly_0123456789abc"),
        chat_id="555",
        allowed_chat_ids=("777",),
    )
