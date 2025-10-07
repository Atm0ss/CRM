"""Database configuration helpers for the CRM application."""

from __future__ import annotations

from flask_sqlalchemy import SQLAlchemy

# A shared SQLAlchemy instance. It will be initialised with the Flask app in
# :func:`init_db` from :mod:`app.__init__`.
db = SQLAlchemy()


def init_db(app) -> None:
    """Initialise the SQLAlchemy instance with the provided Flask app."""
    db.init_app(app)


def reset_database() -> None:
    """Drop and recreate all database tables."""
    from . import create_app  # local import to avoid circular dependency

    app = create_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
