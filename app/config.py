"""Central configuration for development and testing.

Environment variables are read after python-dotenv loads a local .env file.
Secrets stay in the environment and are never written into source.
"""

import os
from typing import TYPE_CHECKING
from sqlalchemy.engine import make_url

from dotenv import load_dotenv

if TYPE_CHECKING:
    from flask import Flask

load_dotenv()


def normalize_database_url(database_url: str | None) -> str | None:
    """Use psycopg 3 for PostgreSQL URLs, including Render's legacy scheme."""
    if not database_url:
        return database_url
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://"):]
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://"):]
    return database_url


class BaseConfig:
    """Settings shared by every environment."""

    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = normalize_database_url(os.environ.get("DATABASE_URL"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Configurable display currency. INR is the default.
    DEFAULT_CURRENCY = os.environ.get("DEFAULT_CURRENCY", "INR")
    ENABLE_PUBLIC_REGISTRATION = os.environ.get("ENABLE_PUBLIC_REGISTRATION", "true").strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def init_app(app: "Flask") -> None:
        """Apply settings that depend on the created application."""


class DevelopmentConfig(BaseConfig):
    """Local development settings."""

    DEBUG = True

    @staticmethod
    def init_app(app: "Flask") -> None:
        """Fill in local-only defaults when the environment does not."""
        if not app.config.get("SECRET_KEY"):
            # Used only on a developer machine. Do not use this value elsewhere.
            app.config["SECRET_KEY"] = "dev-only-not-for-production"
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            database_path = os.path.join(app.instance_path, "financial_reports.db")
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + database_path.replace(
                "\\", "/"
            )


class TestingConfig(BaseConfig):
    """Isolated settings for the automated test suite."""

    TESTING = True
    SECRET_KEY = "testing-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class PostgreSQLTestingConfig(BaseConfig):
    """Opt-in local disposable PostgreSQL test DB; intentionally has no fallback."""
    TESTING = True
    SECRET_KEY = "testing-secret-key"
    SQLALCHEMY_DATABASE_URI = normalize_database_url(os.environ.get("POSTGRES_TEST_DATABASE_URL"))

    @staticmethod
    def init_app(app: "Flask") -> None:
        database_url = app.config.get("SQLALCHEMY_DATABASE_URI")
        if not database_url:
            raise ValueError("POSTGRES_TEST_DATABASE_URL is required for PostgreSQL tests.")
        parsed = make_url(database_url)
        if (
            parsed.drivername != "postgresql+psycopg"
            or parsed.host not in {"localhost", "127.0.0.1", "::1"}
            or parsed.database != "financial_report_generator_test"
        ):
            raise ValueError(
                "PostgreSQL tests require psycopg and the loopback database "
                "'financial_report_generator_test'."
            )


class ProductionConfig(BaseConfig):
    """Fail closed when deployment secrets or database settings are missing."""
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    ENABLE_PUBLIC_REGISTRATION = os.environ.get("ENABLE_PUBLIC_REGISTRATION", "false").strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def init_app(app: "Flask") -> None:
        secret=app.config.get("SECRET_KEY")
        unsafe_defaults={"dev-only-not-for-production","testing-secret-key","replace-with-a-long-random-string"}
        if not secret or len(secret) < 32 or secret in unsafe_defaults or "replace-with-a-long-random-string" in secret:
            raise ValueError("Production requires a SECRET_KEY of at least 32 characters.")
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            raise ValueError("Production requires DATABASE_URL.")


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "postgresql_testing": PostgreSQLTestingConfig,
    "production": ProductionConfig,
}


def get_config(config_name: str) -> type[BaseConfig]:
    """Return the configuration class registered under ``config_name``."""
    try:
        return CONFIG_MAP[config_name]
    except KeyError as exc:
        known = ", ".join(sorted(CONFIG_MAP))
        raise ValueError(
            f"Unknown configuration '{config_name}'. Expected one of: {known}."
        ) from exc
