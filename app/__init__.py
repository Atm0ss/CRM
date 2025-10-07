"""Flask application factory for the CRM system."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Dict

import click
from flask import Flask

from .database import db, init_db
from .models import Client
from .routes import crm_bp
from .remote import ensure_anydesk


def create_app(test_config: Dict | None = None) -> Flask:
    """Create and configure the Flask application."""

    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("CRM_SECRET_KEY", "dev"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "CRM_DATABASE_URL", f"sqlite:///{os.path.abspath('crm.sqlite')}"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )

    if test_config:
        app.config.update(test_config)

    init_db(app)
    app.register_blueprint(crm_bp)

    @app.cli.command("init-db")
    def init_db_command() -> None:
        """Create all database tables."""

        db.create_all()
        print("Database initialised")

    @app.cli.command("seed-db")
    def seed_db_command() -> None:
        """Populate the database with sample data for quick demos."""

        from .seed import seed_database

        seed_database()
        print("Database seeded with demo data")

    @app.cli.command("sync-anydesk-id")
    @click.argument("client_id", type=int)
    def sync_anydesk_id(client_id: int) -> None:
        """Ensure AnyDesk is installed locally and persist the desktop ID for a client."""

        client = Client.query.get(client_id)
        if not client:
            click.echo(f"Клиент с id={client_id} не найден.")
            return

        status = ensure_anydesk()
        if not status.installed:
            click.echo(status.message)
            return

        if status.desk_id:
            client.remote_support_tool = status.tool
            client.remote_desktop_id = status.desk_id
            db.session.commit()
            click.echo(
                f"ID рабочего стола {status.desk_id} сохранён для клиента {client.name}."
            )
        else:
            click.echo(status.message)

    @app.route("/health")
    def health_check():
        return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

    return app
