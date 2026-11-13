"""Presentation-layer blueprint registration."""

from flask import Flask

from app.blueprints.main import main_bp


def register_blueprints(app: Flask) -> None:
    """Attach route modules to the application."""
    app.register_blueprint(main_bp)
