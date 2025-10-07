"""Database models representing the core CRM entities."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .database import db


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class Client(TimestampMixin, db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=True)
    phone = db.Column(db.String(30))
    company_name = db.Column(db.String(120))
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"))
    address = db.Column(db.String(255))
    remote_support_tool = db.Column(db.String(50), default="AnyDesk")
    remote_desktop_id = db.Column(db.String(50))
    theme_preference = db.Column(db.String(20), default="light", nullable=False)
    company = db.relationship("Company", back_populates="clients")
    notes = db.relationship("ClientNote", backref="client", cascade="all, delete")
    assets = db.relationship("Asset", backref="client", cascade="all, delete")
    contracts = db.relationship("ServiceContract", backref="client", cascade="all, delete")
    tickets = db.relationship("Ticket", backref="client", cascade="all, delete")
    tasks = db.relationship("Task", backref="client", cascade="all, delete")
    appointments = db.relationship(
        "Appointment", backref="client", cascade="all, delete"
    )
    monitoring_incidents = db.relationship(
        "MonitoringIncident", back_populates="client", cascade="all, delete"
    )
    knowledge_articles = db.relationship(
        "KnowledgeBaseArticle", back_populates="client", cascade="all, delete"
    )

    def to_dict(self) -> Dict:
        company_payload: Optional[Dict]
        if self.company:
            company_payload = self.company.to_dict()
        elif self.company_name:
            company_payload = {"name": self.company_name}
        else:
            company_payload = None
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "company": company_payload,
            "address": self.address,
            "remote_support_tool": self.remote_support_tool,
            "remote_desktop_id": self.remote_desktop_id,
            "theme_preference": self.theme_preference,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "assets": [asset.to_dict() for asset in self.assets],
            "contracts": [contract.to_dict() for contract in self.contracts],
            "tasks": [task.to_dict() for task in self.tasks],
            "appointments": [
                appointment.to_dict() for appointment in self.appointments
            ],
            "monitoring_incidents": [
                incident.to_dict(include_ticket=True)
                for incident in self.monitoring_incidents
            ],
            "knowledge_base_articles": [
                article.to_dict()
                for article in self.knowledge_articles
                if article.is_published
            ],
        }


class Company(TimestampMixin, db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    industry = db.Column(db.String(120))
    headquarters = db.Column(db.String(255))
    clients = db.relationship("Client", back_populates="company")

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "industry": self.industry,
            "headquarters": self.headquarters,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class ClientNote(TimestampMixin, db.Model):
    __tablename__ = "client_notes"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    author = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text, nullable=False)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "client_id": self.client_id,
            "author": self.author,
            "body": self.body,
            "created_at": self.created_at.isoformat(),
        }


class Asset(TimestampMixin, db.Model):
    __tablename__ = "assets"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    serial_number = db.Column(db.String(120))
    asset_type = db.Column(db.String(120))
    status = db.Column(db.String(50), default="active", nullable=False)
    location = db.Column(db.String(255))

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "client_id": self.client_id,
            "name": self.name,
            "serial_number": self.serial_number,
            "asset_type": self.asset_type,
            "status": self.status,
            "location": self.location,
            "created_at": self.created_at.isoformat(),
        }


class ServiceContract(TimestampMixin, db.Model):
    __tablename__ = "service_contracts"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    support_level = db.Column(db.String(120))

    def to_dict(self) -> Dict:
        today = datetime.utcnow().date()
        return {
            "id": self.id,
            "client_id": self.client_id,
            "title": self.title,
            "description": self.description,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "support_level": self.support_level,
            "is_active": self.start_date <= today <= self.end_date,
        }


class Ticket(TimestampMixin, db.Model):
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    priority = db.Column(db.String(50), default="normal", nullable=False)
    status = db.Column(db.String(50), default="open", nullable=False)
    assigned_to = db.Column(db.String(120))
    due_date = db.Column(db.Date)
    notes = db.relationship("TicketNote", backref="ticket", cascade="all, delete")
    monitoring_incident = db.relationship(
        "MonitoringIncident",
        back_populates="ticket",
        uselist=False,
        foreign_keys="MonitoringIncident.ticket_id",
    )

    def to_dict(self, include_notes: bool = False) -> Dict:
        payload = {
            "id": self.id,
            "client_id": self.client_id,
            "subject": self.subject,
            "description": self.description,
            "priority": self.priority,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if include_notes:
            payload["notes"] = [note.to_dict() for note in self.notes]
        if self.monitoring_incident:
            payload["monitoring_incident"] = self.monitoring_incident.to_dict()
        return payload


class TicketNote(TimestampMixin, db.Model):
    __tablename__ = "ticket_notes"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False)
    author = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text, nullable=False)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "author": self.author,
            "body": self.body,
            "created_at": self.created_at.isoformat(),
        }


class Task(TimestampMixin, db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(50), default="pending", nullable=False)
    priority = db.Column(db.String(50), default="normal", nullable=False)
    due_date = db.Column(db.Date)
    assigned_to = db.Column(db.String(120))
    completed_at = db.Column(db.DateTime)
    created_by = db.Column(db.String(120))

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "client_id": self.client_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "assigned_to": self.assigned_to,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class Appointment(TimestampMixin, db.Model):
    __tablename__ = "appointments"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    start_time = db.Column(db.DateTime, nullable=False)
    duration_minutes = db.Column(db.Integer, default=60, nullable=False)
    status = db.Column(db.String(50), default="scheduled", nullable=False)
    assigned_to = db.Column(db.String(120))
    location = db.Column(db.String(255))
    notes = db.Column(db.Text)

    def to_dict(self, include_client: bool = False) -> Dict:
        end_time = self.start_time + timedelta(minutes=self.duration_minutes)
        payload: Dict = {
            "id": self.id,
            "client_id": self.client_id,
            "title": self.title,
            "description": self.description,
            "start_time": self.start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "duration_minutes": self.duration_minutes,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "location": self.location,
            "notes": self.notes,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if include_client and self.client:
            payload["client"] = {
                "id": self.client.id,
                "name": self.client.name,
                "company": self.client.company.name if self.client.company else None,
            }
        return payload


class User(TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    two_factor_enabled = db.Column(db.Boolean, default=False, nullable=False)
    two_factor_secret = db.Column(db.String(32))
    theme_preference = db.Column(db.String(20), default="light", nullable=False)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "two_factor_enabled": self.two_factor_enabled,
            "theme_preference": self.theme_preference,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class MonitoringIncident(TimestampMixin, db.Model):
    __tablename__ = "monitoring_incidents"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    source = db.Column(db.String(50), default="zabbix", nullable=False)
    external_id = db.Column(db.String(120), nullable=False)
    severity = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default="open", nullable=False)
    occurred_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"))

    client = db.relationship("Client", back_populates="monitoring_incidents")
    ticket = db.relationship(
        "Ticket",
        back_populates="monitoring_incident",
        foreign_keys=ticket_id,
    )

    __table_args__ = (db.UniqueConstraint("source", "external_id", name="uq_incident_source"),)

    def to_dict(self, include_ticket: bool = False) -> Dict:
        payload: Dict = {
            "id": self.id,
            "client_id": self.client_id,
            "source": self.source,
            "external_id": self.external_id,
            "severity": self.severity,
            "message": self.message,
            "status": self.status,
            "occurred_at": self.occurred_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "ticket_id": self.ticket_id,
        }
        if include_ticket and self.ticket:
            payload["ticket"] = self.ticket.to_dict()
        return payload


class KnowledgeBaseArticle(TimestampMixin, db.Model):
    __tablename__ = "knowledge_base_articles"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    summary = db.Column(db.String(255))
    body = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(120))
    tags = db.Column(db.Text)
    author = db.Column(db.String(120))
    is_published = db.Column(db.Boolean, default=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))

    client = db.relationship("Client", back_populates="knowledge_articles")

    def to_dict(self) -> Dict:
        tag_list = [tag.strip() for tag in (self.tags or "").split(",") if tag.strip()]
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "category": self.category,
            "tags": tag_list,
            "author": self.author,
            "is_published": self.is_published,
            "client_id": self.client_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


def serialize_collection(items: List) -> List[Dict]:
    """Serialize a collection of model instances to dictionaries."""

    return [item.to_dict() for item in items]
