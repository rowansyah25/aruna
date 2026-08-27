"""Configuration behaviour, especially the guards the spec demands."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError
from tests.conftest import make_settings

from aruna.core.config import (
    PROJECT_ROOT,
    AppSettings,
    DatabaseSettings,
    ProviderSettings,
    RedisSettings,
    TelegramSettings,
)
from aruna.core.enums import Market


class TestMarkets:
    def test_defaults_to_crypto_saja(self) -> None:
        """Dua pasar sampai 2026-08-25, lalu IDX dikeluarkan.

        IDX adalah separuh saham dari jalur spot, dan jalur itu dicabut atas
        keputusan operator - tidak ada lagi pembaca yang memakai candle atau
        kuotasi IDX untuk memutuskan apa pun. Yang tersisa hanya ongkosnya,
        terukur 900+ ``ingest.quality_rejected`` per jam di luar jam bursa.

        Penolakannya benar; yang salah tetap menanyakannya.
        """
        app = AppSettings(_env_file=None)
        assert app.enabled_markets == (Market.CRYPTO,)

    def test_idx_masih_bisa_dinyalakan_kembali(self) -> None:
        """Dikeluarkan dari bawaan, bukan dihapus dari kosakata: kalau kelak
        ada pembacanya lagi, satu variabel lingkungan cukup."""
        app = AppSettings(_env_file=None, enabled_markets="CRYPTO,IDX")
        assert app.enabled_markets == (Market.CRYPTO, Market.IDX)

    def test_parses_comma_separated(self) -> None:
        app = AppSettings(_env_file=None, enabled_markets="crypto, idx")
        assert app.enabled_markets == (Market.CRYPTO, Market.IDX)

    def test_single_market(self) -> None:
        app = AppSettings(_env_file=None, enabled_markets="IDX")
        assert app.enabled_markets == (Market.IDX,)

    @pytest.mark.parametrize("value", ["FX", "CRYPTO,FX", "fx", "foreign_exchange"])
    def test_forex_aliases_are_refused(self, value: str) -> None:
        """Only ``FOREX`` is legal; the ambiguous aliases stay refused."""
        with pytest.raises(ValidationError, match="write FOREX"):
            AppSettings(_env_file=None, enabled_markets=value)

    def test_canonical_forex_is_accepted(self) -> None:
        """XAUUSD M5 needs the market that was reopened on 2026-08-27."""
        settings = AppSettings(_env_file=None, enabled_markets="CRYPTO,FOREX")
        assert Market.FOREX in settings.enabled_markets

    def test_unknown_market_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="unknown market"):
            AppSettings(_env_file=None, enabled_markets="NASDAQ")

    def test_empty_list_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="at least one market"):
            AppSettings(_env_file=None, enabled_markets="")


class TestPhaseNumber:
    def test_the_two_phase_constants_agree(self) -> None:
        """``aruna.__phase__`` is hand-maintained so the package stays free of
        imports. This is what stops it drifting from the real build stage."""
        import aruna
        from aruna.core.config import CURRENT_PHASE

        assert aruna.__phase__ == CURRENT_PHASE


class TestTradingGuards:
    def test_real_trading_cannot_be_enabled(self) -> None:
        with pytest.raises(ValidationError, match="real order execution is not implemented"):
            AppSettings(_env_file=None, real_trading_enabled=True)

    def test_paper_trading_cannot_be_disabled(self) -> None:
        with pytest.raises(ValidationError, match="no trading mode"):
            AppSettings(_env_file=None, paper_trading_enabled=False)

    def test_defaults_are_paper_only(self) -> None:
        app = AppSettings(_env_file=None)
        assert app.paper_trading_enabled is True
        assert app.real_trading_enabled is False


class TestDatabase:
    def test_defaults_target_laragon_mysql(self) -> None:
        db = DatabaseSettings(_env_file=None)
        assert db.port == 3306
        assert db.user == "root"

    def test_dsn_contains_password_and_safe_dsn_does_not(self) -> None:
        db = DatabaseSettings(_env_file=None, password=SecretStr("hunter2secret"))
        assert "hunter2secret" in db.dsn
        assert "hunter2secret" not in db.safe_dsn
        assert "***" in db.safe_dsn

    def test_safe_dsn_omits_mask_when_no_password(self) -> None:
        db = DatabaseSettings(_env_file=None, password=SecretStr(""))
        assert "***" not in db.safe_dsn

    def test_pool_bounds_are_validated(self) -> None:
        with pytest.raises(ValidationError, match="cannot exceed"):
            DatabaseSettings(_env_file=None, min_pool=20, max_pool=5)

    def test_port_range_is_validated(self) -> None:
        with pytest.raises(ValidationError):
            DatabaseSettings(_env_file=None, port=99999)


class TestConnectKwargs:
    def test_session_is_pinned_to_utc(self) -> None:
        """MySQL DATETIME carries no offset, so the session pin is the only
        thing making stored timestamps unambiguous."""
        kwargs = DatabaseSettings(_env_file=None).connect_kwargs()
        assert "time_zone = '+00:00'" in kwargs["init_command"]

    def test_strict_sql_mode_is_forced(self) -> None:
        """Without strict mode MySQL truncates instead of failing - silent data
        fabrication, which SPEC 4 forbids."""
        kwargs = DatabaseSettings(_env_file=None).connect_kwargs()
        assert "STRICT_ALL_TABLES" in kwargs["init_command"]

    def test_sql_mode_is_not_passed_as_a_driver_parameter(self) -> None:
        """asyncmy interpolates its own sql_mode= into SET sql_mode = <value>
        unquoted, so a comma-separated mode list is a syntax error there."""
        kwargs = DatabaseSettings(_env_file=None).connect_kwargs()
        assert "sql_mode" not in kwargs
        assert "SESSION sql_mode = '" in kwargs["init_command"]

    def test_session_settings_arrive_as_one_statement(self) -> None:
        command = DatabaseSettings(_env_file=None).connect_kwargs()["init_command"]
        assert command.count("SET ") == 1

    def test_command_timeout_becomes_a_server_side_cap(self) -> None:
        kwargs = DatabaseSettings(_env_file=None, command_timeout_sec=12).connect_kwargs()
        assert "max_execution_time = 12000" in kwargs["init_command"]

    def test_database_defaults_to_the_configured_schema(self) -> None:
        kwargs = DatabaseSettings(_env_file=None, name="aruna").connect_kwargs()
        assert kwargs["database"] == "aruna"

    def test_database_can_be_omitted_for_create_database(self) -> None:
        kwargs = DatabaseSettings(_env_file=None).connect_kwargs(database=None)
        assert kwargs["database"] is None

    def test_charset_is_utf8mb4(self) -> None:
        assert DatabaseSettings(_env_file=None).connect_kwargs()["charset"] == "utf8mb4"


class TestRedis:
    def test_url_includes_password_and_safe_url_does_not(self) -> None:
        cfg = RedisSettings(_env_file=None, password=SecretStr("cachepass123"))
        assert "cachepass123" in cfg.url
        assert "cachepass123" not in cfg.safe_url

    def test_a_password_with_url_syntax_is_percent_encoded(self) -> None:
        """Unencoded, a strong password changes what the URL means.

        ``p@ss/w0rd`` parses as host "ss" on the way to database "w0rd", and
        the resulting error carries a fragment of the password into a startup
        traceback that escapes before logging is configured (SPEC 43).
        """
        from urllib.parse import urlsplit

        cfg = RedisSettings(
            _env_file=None,
            host="127.0.0.1",
            port=6379,
            db=0,
            password=SecretStr("p@ss/w0rd#1?x"),
        )
        parts = urlsplit(cfg.url)
        assert parts.hostname == "127.0.0.1"
        assert parts.port == 6379
        assert parts.path == "/0"
        # And it still round-trips to the real password.
        assert parts.password is not None
        from urllib.parse import unquote

        assert unquote(parts.password) == "p@ss/w0rd#1?x"

    def test_an_ordinary_password_is_unchanged(self) -> None:
        cfg = RedisSettings(_env_file=None, password=SecretStr("cachepass123"))
        assert ":cachepass123@" in cfg.url

    def test_namespaced_keys(self) -> None:
        cfg = RedisSettings(_env_file=None, namespace="aruna")
        assert cfg.key("health", "latest") == "aruna:health:latest"


class TestTelegram:
    def test_inactive_without_token(self) -> None:
        cfg = TelegramSettings(_env_file=None, enabled=True, bot_token=SecretStr(""))
        assert cfg.active is False

    def test_active_with_token(self, telegram_settings: TelegramSettings) -> None:
        assert telegram_settings.active is True

    def test_authorized_ids_include_primary_chat(
        self, telegram_settings: TelegramSettings
    ) -> None:
        assert telegram_settings.authorized_chat_ids == frozenset({"555", "777"})

    def test_authorization_accepts_int_and_str(
        self, telegram_settings: TelegramSettings
    ) -> None:
        assert telegram_settings.is_authorized(555) is True
        assert telegram_settings.is_authorized("777") is True
        assert telegram_settings.is_authorized("999") is False

    def test_empty_allowlist_fails_closed(self) -> None:
        """No configured chat must authorize nobody, not everybody."""
        cfg = TelegramSettings(_env_file=None, chat_id="", allowed_chat_ids=())
        assert cfg.authorized_chat_ids == frozenset()
        assert cfg.is_authorized("555") is False


class TestKredensialCryptoTidakAda:
    """PASAL 41: tidak ada jalur eksekusi, jadi tidak ada slot kredensialnya.

    ``crypto_provider_api_key`` dan ``crypto_provider_api_secret`` pernah ada di
    :class:`ProviderSettings` dengan alasan: ``Settings.secrets`` menyuapi log
    redactor, dan nama yang tidak dikenal redactor adalah nama yang bisa
    tercetak. Alasan itu tidak berdiri, dan test kedua di bawah adalah
    pembuktiannya - ``extra="ignore"`` membuat variabel itu tidak pernah masuk
    ke proses, sehingga tidak ada apa pun untuk dicetak maupun disensor.

    Yang tersisa kalau field-nya dipertahankan hanyalah kerugiannya: slot
    bernama yang mengundang operator menempelkan kunci API Binance sungguhan ke
    sistem yang memang tidak punya cara memakainya.
    """

    def test_field_kredensial_crypto_tidak_ada_lagi(self) -> None:
        providers = ProviderSettings(_env_file=None)
        assert not hasattr(providers, "crypto_provider_api_key")
        assert not hasattr(providers, "crypto_provider_api_secret")
        assert providers.crypto_provider == "binance-spot"

    def test_variabel_yatim_di_env_tidak_pernah_dimuat(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Justru inilah alasan menghapusnya aman.

        Sebuah ``.env`` warisan yang masih memuat ARUNA_CRYPTO_PROVIDER_API_KEY
        tidak menjadi nilai di mana pun di dalam proses, jadi tidak ada yang
        perlu diketahui redactor. Kalau ``extra`` pernah dilonggarkan menjadi
        ``allow``, test ini merah - dan memang harus, karena saat itu nilai
        rahasia benar-benar masuk ke memori tanpa terdaftar di
        :meth:`Settings.secrets`.
        """
        monkeypatch.setenv("ARUNA_CRYPTO_PROVIDER_API_KEY", "kunciapiyangbocor")
        monkeypatch.setenv("ARUNA_CRYPTO_PROVIDER_API_SECRET", "rahasiayangbocor")

        providers = ProviderSettings(_env_file=None)
        settings = make_settings(providers=providers)

        dumped = str(providers.model_dump())
        assert "crypto_provider_api" not in dumped
        assert "kunciapiyangbocor" not in dumped
        assert "kunciapiyangbocor" not in settings.secrets()
        assert "rahasiayangbocor" not in settings.secrets()

    def test_env_example_tidak_menyediakan_slotnya_lagi(self) -> None:
        """Separuh temuan yang tidak tuntas kalau hanya field-nya dihapus.

        Menghapus field dari :class:`ProviderSettings` membuat variabelnya tidak
        berdaya, tapi TIDAK menghapus undangannya. ``.env.example`` adalah
        berkas yang benar-benar disalin operator, jadi slot bernama di sana
        tetap mengundang kunci API Binance sungguhan ditempelkan - persis
        kerugian yang penghapusan ini ingin hilangkan, dan persis yang
        dinyatakan README ("removed rather than left empty, because a named slot
        is an invitation").

        Merah kalau slotnya kembali, entah sebagai baris aktif atau sebagai
        contoh yang dikomentari.
        """
        contoh = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

        aktif = [
            baris
            for baris in contoh.splitlines()
            if baris.strip().startswith("ARUNA_CRYPTO_PROVIDER_API")
        ]
        assert aktif == [], f"slot kredensial crypto kembali ke .env.example: {aktif}"

    def test_env_example_mengatakan_kenapa_slotnya_tidak_ada(self) -> None:
        """Ketiadaan harus terbaca sebagai keputusan, bukan sebagai kelalaian.

        Tanpa kalimat ini, operator berikutnya yang mencari tempat menaruh kunci
        hanya menemukan kekosongan - dan kekosongan adalah hal yang orang isi.
        """
        contoh = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        assert "PASAL 41" in contoh
        assert "public market-data endpoints only" in contoh


class TestSecretsAndWarnings:
    def test_secrets_collects_every_credential(self) -> None:
        settings = make_settings(
            db=DatabaseSettings(_env_file=None, password=SecretStr("dbpassword123")),
            redis=RedisSettings(_env_file=None, password=SecretStr("redispassword123")),
            providers=ProviderSettings(
                _env_file=None,
                idx_provider="example",
                idx_provider_api_key=SecretStr("idxkey1234567890"),
            ),
        )
        secrets = settings.secrets()
        assert "dbpassword123" in secrets
        assert "redispassword123" in secrets
        assert "idxkey1234567890" in secrets

    def test_short_secrets_are_not_collected(self) -> None:
        """Scrubbing a 3-character value would mangle unrelated log text."""
        settings = make_settings(
            db=DatabaseSettings(_env_file=None, password=SecretStr("abc"))
        )
        assert settings.secrets() == frozenset()

    def test_empty_password_is_only_acceptable_in_development(self) -> None:
        """Laragon's root-with-no-password is normal locally and must never
        follow the project to a VPS."""
        local = make_settings()
        assert any("normal for a local Laragon" in n for n in local.phase_notices())
        assert local.startup_warnings() == []

        staged = make_settings(app=AppSettings(_env_file=None, env="staging"))
        joined = " ".join(staged.startup_warnings())
        assert "reachable without a password" in joined

    def test_all_providers_are_configured_by_default(self) -> None:
        """Since PHASE 4 every stream has a default source, so nothing should
        report itself unavailable out of the box."""
        settings = make_settings()
        assert all(settings.providers.configured.values())
        assert not any("DATA SOURCE UNAVAILABLE" in n for n in settings.phase_notices())
        assert settings.startup_warnings() == []

    def test_a_blank_provider_is_a_phase_notice_not_a_warning(self) -> None:
        """A deliberately disabled stream is reported (SPEC 4) without
        degrading health - it is a choice, not a fault."""
        settings = make_settings(
            providers=ProviderSettings(_env_file=None, news_provider="")
        )
        joined = " ".join(settings.phase_notices())
        assert "DATA SOURCE UNAVAILABLE" in joined
        assert "news" in joined
        assert settings.startup_warnings() == []

    def test_headless_telegram_is_a_phase_notice(self) -> None:
        settings = make_settings()
        assert any("headless" in n for n in settings.phase_notices())

    def test_active_telegram_without_allowlist_warns(self) -> None:
        settings = make_settings(
            telegram=TelegramSettings(
                _env_file=None,
                bot_token=SecretStr("123456789:AAFakeTokenForTestsOnly_0123456789abc"),
                chat_id="",
            )
        )
        joined = " ".join(settings.startup_warnings())
        assert "no authorized chat ids" in joined

    def test_describe_never_leaks_a_secret(self) -> None:
        settings = make_settings(
            db=DatabaseSettings(_env_file=None, password=SecretStr("supersecretvalue")),
            telegram=TelegramSettings(
                _env_file=None, bot_token=SecretStr("123456789:AAsecrettokenvalue1234567890")
            ),
        )
        rendered = str(settings.describe())
        assert "supersecretvalue" not in rendered
        assert "AAsecrettokenvalue1234567890" not in rendered

    def test_describe_reports_paper_mode(self) -> None:
        assert make_settings().describe()["trading_mode"] == "PAPER"
