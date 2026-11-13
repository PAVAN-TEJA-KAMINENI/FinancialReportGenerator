"""Verify that the application factory starts a usable app."""

from app import create_app
from app.extensions import db


def test_application_starts():
    """A testing app serves the home page with SQLAlchemy configured."""
    app = create_app("testing")

    assert app.testing is True
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"
    assert app.config["DEFAULT_CURRENCY"] == "INR"
    assert "sqlalchemy" in app.extensions

    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"Financial Report Generator" in response.data
    assert b"bootstrap.min.css" in response.data

    static_response = client.get("/static/css/app.css")
    assert static_response.status_code == 200

    with app.app_context():
        assert db.engine.url.drivername == "sqlite"
