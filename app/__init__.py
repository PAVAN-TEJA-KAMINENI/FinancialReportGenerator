"""Application factory for the Financial Report Generator."""

import os

from flask import Flask

from app.blueprints import register_blueprints
from app.config import get_config
from app.extensions import db


def create_app(config_name: str | None = None) -> Flask:
    """Create a configured Flask application.

    ``config_name`` selects ``development`` or ``testing``. When it is
    omitted, ``FLASK_CONFIG`` is used and defaults to development.
    """
    if config_name is None:
        config_name = os.environ.get("FLASK_CONFIG", "development")

    app = Flask(
        __name__,
        instance_relative_config=True,
        static_folder="static",
        template_folder="templates",
    )
    config_class = get_config(config_name)
    app.config.from_object(config_class)
    config_class.init_app(app)

    # The instance folder holds the local SQLite file and is not committed.
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    register_blueprints(app)
    return app
