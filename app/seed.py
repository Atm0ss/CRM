"""Seed data helpers for the CRM system."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .database import db
from .models import (
    Appointment,
    Asset,
    Client,
    ClientNote,
    Company,
    KnowledgeBaseArticle,
    MonitoringIncident,
    ServiceContract,
    Ticket,
    TicketNote,
    Task,
    User,
)
from werkzeug.security import generate_password_hash


def seed_database() -> None:
    """Populate the database with demo data."""

    db.drop_all()
    db.create_all()

    acme_corp = Company(name="Acme", industry="Manufacturing", headquarters="Springfield")
    globex_corp = Company(
        name="Globex", industry="Technology", headquarters="Metropolis"
    )
    db.session.add_all([acme_corp, globex_corp])
    db.session.flush()

    acme = Client(
        name="Acme Industries",
        email="it@acme.test",
        phone="+1-555-1010",
        company=acme_corp,
        address="742 Evergreen Terrace",
        remote_support_tool="AnyDesk",
        remote_desktop_id="123-456-789",
        theme_preference="dark",
    )
    globex = Client(
        name="Globex Corp",
        email="support@globex.test",
        phone="+1-555-2020",
        company=globex_corp,
        address="123 Main Street",
        remote_support_tool="AnyDesk",
        remote_desktop_id="987-654-321",
        theme_preference="light",
    )

    db.session.add_all([acme, globex])
    db.session.flush()

    db.session.add_all(
        [
            ClientNote(client_id=acme.id, author="Admin", body="Prefers email updates"),
            ClientNote(client_id=globex.id, author="Admin", body="Monthly on-site visits"),
        ]
    )

    db.session.add_all(
        [
            Asset(
                client_id=acme.id,
                name="Firewall",
                asset_type="Network",
                serial_number="FW-ACME-001",
                location="HQ Server Room",
            ),
            Asset(
                client_id=globex.id,
                name="Exchange Server",
                asset_type="Server",
                serial_number="EX-GLOBEX-002",
                location="HQ Rack 4",
            ),
        ]
    )

    db.session.add_all(
        [
            ServiceContract(
                client_id=acme.id,
                title="Gold Support",
                description="24/7 support with 1 hour response",
                start_date=date.today(),
                end_date=date.today() + timedelta(days=365),
                support_level="Gold",
            ),
            ServiceContract(
                client_id=globex.id,
                title="Silver Support",
                description="Business hours support",
                start_date=date.today(),
                end_date=date.today() + timedelta(days=180),
                support_level="Silver",
            ),
        ]
    )

    ticket = Ticket(
        client_id=acme.id,
        subject="VPN outage",
        description="Remote staff cannot connect",
        priority="high",
        status="open",
        assigned_to="Alice",
        due_date=date.today() + timedelta(days=1),
    )
    db.session.add(ticket)
    db.session.flush()
    db.session.add(
        TicketNote(
            ticket_id=ticket.id,
            author="Alice",
            body="Investigating VPN gateway logs",
        )
    )

    db.session.add_all(
        [
            Task(
                client_id=acme.id,
                title="Install security patches",
                description="Patch all Acme servers to the latest LTS release",
                status="in_progress",
                priority="high",
                due_date=date.today() + timedelta(days=3),
                assigned_to="Alice",
                created_by="System",
            ),
            Task(
                client_id=globex.id,
                title="Prepare quarterly report",
                description="Compile uptime and SLA metrics for Globex",
                status="pending",
                priority="normal",
                due_date=date.today() + timedelta(days=14),
                assigned_to="Bob",
                created_by="System",
            ),
        ]
    )

    db.session.add_all(
        [
            Appointment(
                client_id=acme.id,
                title="Monthly on-site maintenance",
                description="Check backups and firmware levels",
                start_time=datetime.utcnow() + timedelta(days=3, hours=2),
                duration_minutes=120,
                status="scheduled",
                assigned_to="Alice",
                location="Acme HQ",
                notes="Bring spare SSDs",
            ),
            Appointment(
                client_id=globex.id,
                title="Quarterly strategy call",
                description="Review SLA metrics and renewal options",
                start_time=datetime.utcnow() - timedelta(days=5),
                duration_minutes=90,
                status="completed",
                assigned_to="Bob",
                location="Video conference",
                notes="Share roadmap deck",
            ),
        ]
    )

    monitoring_incident = MonitoringIncident(
        client_id=acme.id,
        source="zabbix",
        external_id="EVT-1001",
        severity="high",
        message="Zabbix detected packet loss on core router",
        status="open",
        occurred_at=datetime.utcnow() - timedelta(hours=2),
    )
    monitoring_incident.ticket = ticket
    db.session.add(monitoring_incident)

    db.session.add_all(
        [
            KnowledgeBaseArticle(
                title="Перезапуск VPN-шлюза",
                summary="Шаги восстановления после обрыва VPN",
                body="""1. Проверить состояние туннелей.
2. Перезапустить службу strongSwan.
3. Убедиться в обновлении политик на Zabbix.""",
                category="Сеть",
                tags="vpn,incident response",
                author="Alice",
                client_id=acme.id,
            ),
            KnowledgeBaseArticle(
                title="Регламент ежемесячного обслуживания",
                summary="Чек-лист выездного инженера",
                body="""- Проверка резервного копирования.
- Обновление прошивок оборудования.
- Тестирование восстановления по плану DR.""",
                category="Обслуживание",
                tags="maintenance,checklist",
                author="Bob",
                client_id=None,
            ),
        ]
    )

    admin_user = User(
        username="admin",
        email="admin@example.com",
        password_hash=generate_password_hash("adminpass"),
        theme_preference="dark",
    )
    db.session.add(admin_user)

    db.session.commit()
