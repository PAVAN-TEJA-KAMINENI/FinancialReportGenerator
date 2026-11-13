"""Central configuration for development and testing.

Environment variables are read after python-dotenv loads a local .env file.
Secrets stay in the environment and are never written into source.
"""

import os
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:
    from flask import Flask

load_dotenv()


class BaseConfig:
    """Settings shared by every environment."""

    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Configurable display currency. INR is the default.
    DEFAULT_CURRENCY = os.environ.get("DEFAULT_CURRENCY", "INR")

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


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
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
