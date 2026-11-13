"""Verify that the application factory starts a usable app."""

import os

import pytest
from flask import Flask

from app import create_app
from app.config import (
    DevelopmentConfig,
    PostgreSQLTestingConfig,
    ProductionConfig,
    get_config,
    normalize_database_url,
)
from app.extensions import db


def test_application_starts():
    """A testing app serves the home page with SQLAlchemy configured."""
    app = create_app("testing")

    assert app.testing is True
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"
    assert app.config["DEFAULT_CURRENCY"] == "INR"
    assert "sqlalchemy" in app.extensions
    assert "migrate" in app.extensions

    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"Financial Report Generator" in response.data
    assert b"bootstrap.min.css" in response.data

    static_response = client.get("/static/css/app.css")
    assert static_response.status_code == 200

    with app.app_context():
        assert db.engine.url.drivername == "sqlite"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("postgres://user:pass@host/db", "postgresql+psycopg://user:pass@host/db"),
        ("postgresql://user:pass@host/db", "postgresql+psycopg://user:pass@host/db"),
        ("postgresql+psycopg://user:pass@host/db", "postgresql+psycopg://user:pass@host/db"),
        ("sqlite:///local.db", "sqlite:///local.db"),
        (None, None),
    ],
)
def test_database_url_normalization(source, expected):
    assert normalize_database_url(source) == expected


def test_development_configuration_keeps_instance_sqlite_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = Flask(__name__, instance_path=str(tmp_path / "instance"))
    app.config.from_object(DevelopmentConfig)
    app.config["SQLALCHEMY_DATABASE_URI"] = None

    DevelopmentConfig.init_app(app)

    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///" + str(
        tmp_path / "instance" / "financial_reports.db"
    ).replace("\\", "/")
    assert not os.path.exists(tmp_path / "instance" / "financial_reports.db")


def test_configuration_selection_and_production_secret_validation():
    assert get_config("production") is ProductionConfig
    assert get_config("development") is DevelopmentConfig
    assert get_config("postgresql_testing") is PostgreSQLTestingConfig

    app = Flask(__name__)
    app.config.update(SECRET_KEY="testing-secret-key", SQLALCHEMY_DATABASE_URI="postgresql+psycopg://example")
    with pytest.raises(ValueError, match="SECRET_KEY"):
        ProductionConfig.init_app(app)


@pytest.mark.parametrize(
    "database_url",
    [
        None,
        "postgresql+psycopg://user:pass@example.com/financial_report_generator_test",
        "postgresql+psycopg://user:pass@127.0.0.1/production",
        "postgresql://user:pass@127.0.0.1/financial_report_generator_test",
    ],
)
def test_postgresql_test_config_rejects_unsafe_database_targets(database_url):
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url

    with pytest.raises(ValueError):
        PostgreSQLTestingConfig.init_app(app)


def test_postgresql_test_config_accepts_only_named_loopback_database():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "postgresql+psycopg://frg_test:frg_test@127.0.0.1:55432/"
        "financial_report_generator_test"
    )

    PostgreSQLTestingConfig.init_app(app)


def test_not_found_uses_generic_error_page():
    app = create_app("testing")
    response = app.test_client().get("/page-that-does-not-exist")

    assert response.status_code == 404
    assert b"could not be found" in response.data


def test_internal_error_logs_details_but_returns_generic_message(caplog):
    app = create_app("testing")
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.get("/explode")
    def explode():
        raise RuntimeError("sensitive database detail")

    response = app.test_client().get("/explode")

    assert response.status_code == 500
    assert b"Something went wrong" in response.data
    assert b"sensitive database detail" not in response.data
    assert "sensitive database detail" in caplog.text
