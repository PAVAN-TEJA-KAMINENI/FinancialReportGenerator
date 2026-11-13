"""Application factory for the Financial Report Generator."""

import os
import click

from flask import Flask, render_template

from app.blueprints import register_blueprints
from app.config import get_config
from app.extensions import db, login_manager, csrf, migrate


def create_app(config_name: str | None = None) -> Flask:
    """Create a configured Flask application.

    ``config_name`` selects ``development``, ``testing`` or ``production``. When it is
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
    migrate.init_app(app, db, compare_type=True)
    login_manager.init_app(app)
    login_manager.login_view = "main.login"
    csrf.init_app(app)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=10 * 1024 * 1024)
    if config_name != "development":
        app.config.update(SESSION_COOKIE_SECURE=True)

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message="The page you requested could not be found."), 404

    @app.errorhandler(500)
    def internal_error(error):
        original = getattr(error, "original_exception", None)
        if original is not None:
            app.logger.error(
                "Unhandled application error",
                exc_info=(type(original), original, original.__traceback__),
            )
        else:
            app.logger.error("Internal server error: %s", error)
        db.session.rollback()
        return render_template("error.html", code=500, message="Something went wrong. Please try again later."), 500
    from app.models import User, Company
    from app.services import seed_accounts
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.cli.command("create-admin")
    @click.option("--company", prompt=True, help="Company name")
    @click.option("--name", prompt=True, help="Administrator name")
    @click.option("--email", prompt=True, help="Administrator email")
    @click.password_option("--password", confirmation_prompt=True)
    def create_admin(company, name, email, password):
        """Create the first company and administrator after `flask db upgrade`."""
        normalized_email=email.strip().lower()
        if not company.strip() or not name.strip() or "@" not in normalized_email or len(password)<10:
            raise click.ClickException("Company, name, valid email, and a password of at least 10 characters are required.")
        if User.query.filter_by(email=normalized_email).first():
            raise click.ClickException("That email is already registered.")
        new_company=Company(name=company.strip(),currency=app.config.get("DEFAULT_CURRENCY","INR"))
        db.session.add(new_company); db.session.flush()
        admin=User(company_id=new_company.id,email=normalized_email,name=name.strip(),role="administrator")
        admin.set_password(password); db.session.add(admin); db.session.flush(); seed_accounts(new_company); db.session.commit()
        click.echo(f"Administrator created for {new_company.name}.")
    register_blueprints(app)
    return app
