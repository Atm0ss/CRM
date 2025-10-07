"""Blueprint with all CRM related routes."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, abort, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

import pyotp

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
    serialize_collection,
)
from .remote import ensure_anydesk

crm_bp = Blueprint("crm", __name__, url_prefix="/api")


ALLOWED_APPOINTMENT_STATUSES = {"scheduled", "completed", "cancelled"}
SEVERITY_PRIORITY_MAP = {
    "disaster": "high",
    "high": "high",
    "average": "normal",
    "warning": "normal",
    "information": "low",
    "not_classified": "low",
}


def calculate_churn_risk(
    client: Client,
    tickets: list[Ticket],
    tasks: list[Task],
    appointments: list[Appointment],
) -> Tuple[float, str]:
    """Return a churn probability and label based on lightweight heuristics."""

    open_tickets = sum(1 for ticket in tickets if ticket.status == "open")
    high_priority = sum(
        1 for ticket in tickets if ticket.status == "open" and ticket.priority == "high"
    )
    overdue_tasks = sum(
        1
        for task in tasks
        if task.status != "completed"
        and task.due_date
        and task.due_date < datetime.utcnow().date()
    )
    missed_appointments = sum(1 for appt in appointments if appt.status == "cancelled")
    days_since_touch = (datetime.utcnow() - client.updated_at).days

    probability = 0.05
    probability += min(0.3, open_tickets * 0.05)
    probability += min(0.3, high_priority * 0.1)
    probability += min(0.2, overdue_tasks * 0.08)
    probability += 0.1 if missed_appointments else 0
    probability += 0.05 if days_since_touch > 30 else 0
    probability = min(probability, 0.95)

    if probability >= 0.6:
        label = "high"
    elif probability >= 0.35:
        label = "medium"
    else:
        label = "low"
    return probability, label


def calculate_engineer_load(tasks: list[Task], appointments: list[Appointment]) -> Dict[str, Any]:
    """Derive engineer utilisation metrics."""

    workload = defaultdict(lambda: {"open_tasks": 0, "scheduled_minutes": 0})
    for task in tasks:
        if task.status != "completed" and task.assigned_to:
            workload[task.assigned_to]["open_tasks"] += 1
    for appointment in appointments:
        if appointment.status == "scheduled" and appointment.assigned_to:
            workload[appointment.assigned_to]["scheduled_minutes"] += appointment.duration_minutes
    forecasts = []
    for engineer, data in workload.items():
        utilisation = min(1.0, (data["scheduled_minutes"] / (8 * 60)))
        score = min(1.0, (data["open_tasks"] * 0.05) + utilisation)
        status = "balanced"
        if score >= 0.8:
            status = "overloaded"
        elif score <= 0.35:
            status = "underutilised"
        forecasts.append(
            {
                "engineer": engineer,
                "open_tasks": data["open_tasks"],
                "scheduled_minutes": data["scheduled_minutes"],
                "utilisation": round(utilisation, 2),
                "status": status,
            }
        )
    return {"engineers": forecasts}


def parse_json() -> Dict[str, Any]:
    if not request.is_json:
        abort(400, description="Request must be JSON")
    return request.get_json()  # type: ignore[return-value]


def sanitise_theme(value: Optional[str]) -> str:
    if value and value.lower() in {"light", "dark"}:
        return value.lower()
    return "light"


def sanitise_appointment_status(value: Optional[str]) -> str:
    if not value:
        return "scheduled"
    status = value.lower()
    if status not in ALLOWED_APPOINTMENT_STATUSES:
        abort(
            400,
            description=(
                "Unsupported appointment status. "
                "Use one of: scheduled, completed, cancelled"
            ),
        )
    return status


def parse_iso_datetime(value: str, field_name: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        abort(400, description=f"Invalid {field_name}: {exc}")
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def require_company(company_id: int) -> Company:
    company = Company.query.get(company_id)
    if not company:
        abort(404, description=f"Company with id {company_id} not found")
    return company


def require_user(user_id: int) -> User:
    user = User.query.get(user_id)
    if not user:
        abort(404, description=f"User with id {user_id} not found")
    return user


@crm_bp.route("/clients", methods=["GET"])
def list_clients():
    clients = Client.query.order_by(Client.name).all()
    return jsonify(serialize_collection(clients))


@crm_bp.route("/clients", methods=["POST"])
def create_client():
    data = parse_json()
    required_fields = ["name", "email"]
    if any(field not in data for field in required_fields):
        abort(400, description="Missing required client fields")

    company: Optional[Company] = None
    if data.get("company_id"):
        try:
            company_id = int(data["company_id"])
        except (TypeError, ValueError):
            abort(400, description="Invalid company id")
        company = require_company(company_id)

    client = Client(
        name=data["name"],
        email=data["email"],
        phone=data.get("phone"),
        company=company,
        company_name=data.get("company_name") or data.get("company"),
        address=data.get("address"),
        theme_preference=sanitise_theme(data.get("theme_preference")),
    )
    db.session.add(client)
    db.session.commit()
    return jsonify(client.to_dict()), 201


@crm_bp.route("/integrations/zabbix/events", methods=["POST"])
def ingest_zabbix_event():
    data = parse_json()
    required_fields = ["event_id", "client_id", "severity", "message"]
    if any(field not in data for field in required_fields):
        abort(400, description="Missing required Zabbix event fields")

    try:
        client_id = int(data["client_id"])
    except (TypeError, ValueError):
        abort(400, description="Invalid client id")

    client = Client.query.get_or_404(client_id)
    severity = str(data["severity"]).lower()
    priority = SEVERITY_PRIORITY_MAP.get(severity, "normal")

    existing = MonitoringIncident.query.filter_by(
        source="zabbix", external_id=str(data["event_id"])
    ).first()
    if existing:
        if data.get("status"):
            new_status = str(data["status"]).lower()
            existing.status = new_status
            if existing.ticket and new_status in {"resolved", "ok"}:
                existing.ticket.status = "resolved"
        existing.severity = severity
        existing.message = data["message"]
        db.session.commit()
        return jsonify(existing.to_dict(include_ticket=True))

    ticket_subject = data.get("problem") or f"Zabbix incident {data['event_id']}"
    ticket_description = data["message"]
    ticket = Ticket(
        client_id=client.id,
        subject=f"[Zabbix] {ticket_subject}",
        description=ticket_description,
        priority=priority,
        status="open",
        assigned_to=data.get("assigned_to") or "Monitoring",
    )

    if priority == "high":
        ticket.due_date = datetime.utcnow().date() + timedelta(days=1)

    occurred_at = datetime.utcnow()
    if data.get("occurred_at"):
        try:
            occurred_at = datetime.fromisoformat(data["occurred_at"])
        except ValueError as exc:
            abort(400, description=f"Invalid occurred_at: {exc}")

    incident = MonitoringIncident(
        client_id=client.id,
        source="zabbix",
        external_id=str(data["event_id"]),
        severity=severity,
        message=data["message"],
        status=str(data.get("status", "open")).lower(),
        occurred_at=occurred_at,
    )

    incident.ticket = ticket
    db.session.add_all([ticket, incident])
    db.session.commit()
    return jsonify(incident.to_dict(include_ticket=True)), 201


@crm_bp.route("/clients/<int:client_id>", methods=["GET"])
def get_client(client_id: int):
    client = Client.query.get_or_404(client_id)
    return jsonify(client.to_dict())


@crm_bp.route("/clients/<int:client_id>/assets", methods=["GET"])
def list_client_assets(client_id: int):
    Client.query.get_or_404(client_id)
    assets = (
        Asset.query.filter_by(client_id=client_id)
        .order_by(Asset.created_at.desc())
        .all()
    )
    return jsonify([asset.to_dict() for asset in assets])


@crm_bp.route("/clients/<int:client_id>", methods=["PUT"])
def update_client(client_id: int):
    client = Client.query.get_or_404(client_id)
    data = parse_json()
    for field in ["name", "email", "phone", "address"]:
        if field in data:
            setattr(client, field, data[field])
    if "company_name" in data or "company" in data:
        client.company_name = data.get("company_name") or data.get("company")
    if "company_id" in data:
        if data["company_id"] is None:
            client.company = None
        else:
            try:
                company_id = int(data["company_id"])
            except (TypeError, ValueError):
                abort(400, description="Invalid company id")
            client.company = require_company(company_id)
    if "theme_preference" in data:
        client.theme_preference = sanitise_theme(data["theme_preference"])
    db.session.commit()
    return jsonify(client.to_dict())


@crm_bp.route("/monitoring/incidents", methods=["GET"])
def list_monitoring_incidents():
    query = MonitoringIncident.query
    if request.args.get("client_id"):
        try:
            client_id = int(request.args["client_id"])
        except (TypeError, ValueError):
            abort(400, description="Invalid client id filter")
        query = query.filter_by(client_id=client_id)
    if request.args.get("status"):
        query = query.filter_by(status=request.args["status"].lower())
    incidents = query.order_by(MonitoringIncident.occurred_at.desc()).all()
    return jsonify([incident.to_dict(include_ticket=True) for incident in incidents])


@crm_bp.route("/monitoring/incidents/<int:incident_id>", methods=["PATCH"])
def update_monitoring_incident(incident_id: int):
    incident = MonitoringIncident.query.get_or_404(incident_id)
    data = parse_json()
    if "status" in data:
        incident.status = str(data["status"]).lower()
    if "severity" in data:
        incident.severity = str(data["severity"]).lower()
    if "message" in data:
        incident.message = data["message"]
    if "ticket_id" in data:
        if data["ticket_id"] is None:
            incident.ticket = None
        else:
            try:
                ticket_id = int(data["ticket_id"])
            except (TypeError, ValueError):
                abort(400, description="Invalid ticket id")
            ticket = Ticket.query.get_or_404(ticket_id)
            incident.ticket = ticket
    db.session.commit()
    return jsonify(incident.to_dict(include_ticket=True))


@crm_bp.route("/clients/<int:client_id>", methods=["DELETE"])
def delete_client(client_id: int):
    client = Client.query.get_or_404(client_id)
    db.session.delete(client)
    db.session.commit()
    return "", 204


@crm_bp.route("/clients/<int:client_id>/assets", methods=["POST"])
def create_asset(client_id: int):
    Client.query.get_or_404(client_id)
    data = parse_json()
    asset = Asset(
        client_id=client_id,
        name=data.get("name", "Unnamed asset"),
        serial_number=data.get("serial_number"),
        asset_type=data.get("asset_type"),
        status=data.get("status", "active"),
        location=data.get("location"),
    )
    db.session.add(asset)
    db.session.commit()
    return jsonify(asset.to_dict()), 201


@crm_bp.route("/clients/<int:client_id>/contracts", methods=["GET"])
def list_client_contracts(client_id: int):
    Client.query.get_or_404(client_id)
    contracts = (
        ServiceContract.query.filter_by(client_id=client_id)
        .order_by(ServiceContract.start_date.desc())
        .all()
    )
    return jsonify([contract.to_dict() for contract in contracts])


@crm_bp.route("/clients/<int:client_id>/contracts", methods=["POST"])
def create_contract(client_id: int):
    Client.query.get_or_404(client_id)
    data = parse_json()
    try:
        start_date = datetime.fromisoformat(data["start_date"]).date()
        end_date = datetime.fromisoformat(data["end_date"]).date()
    except (KeyError, ValueError) as exc:
        abort(400, description=f"Invalid contract dates: {exc}")

    contract = ServiceContract(
        client_id=client_id,
        title=data.get("title", "Service Contract"),
        description=data.get("description"),
        start_date=start_date,
        end_date=end_date,
        support_level=data.get("support_level"),
    )
    db.session.add(contract)
    db.session.commit()
    return jsonify(contract.to_dict()), 201


@crm_bp.route("/clients/<int:client_id>/notes", methods=["GET"])
def list_client_notes(client_id: int):
    Client.query.get_or_404(client_id)
    notes = (
        ClientNote.query.filter_by(client_id=client_id)
        .order_by(ClientNote.created_at.desc())
        .all()
    )
    return jsonify([note.to_dict() for note in notes])


@crm_bp.route("/clients/<int:client_id>/notes", methods=["POST"])
def create_client_note(client_id: int):
    Client.query.get_or_404(client_id)
    data = parse_json()
    note = ClientNote(
        client_id=client_id,
        author=data.get("author", "System"),
        body=data.get("body", ""),
    )
    db.session.add(note)
    db.session.commit()
    return jsonify(note.to_dict()), 201


@crm_bp.route("/clients/<int:client_id>/tickets", methods=["GET"])
def list_client_tickets(client_id: int):
    Client.query.get_or_404(client_id)
    tickets = Ticket.query.filter_by(client_id=client_id).order_by(Ticket.created_at).all()
    return jsonify([ticket.to_dict(include_notes=True) for ticket in tickets])


@crm_bp.route("/clients/<int:client_id>/overview", methods=["GET"])
def client_overview(client_id: int):
    client = Client.query.get_or_404(client_id)
    today = datetime.utcnow().date()
    now = datetime.utcnow()

    client_payload = client.to_dict()
    client_payload["notes"] = [
        note.to_dict()
        for note in sorted(client.notes, key=lambda note: note.created_at, reverse=True)
    ]

    tasks = (
        Task.query.filter_by(client_id=client_id)
        .order_by(Task.due_date, Task.priority.desc(), Task.created_at.desc())
        .all()
    )

    tickets = (
        Ticket.query.filter_by(client_id=client_id)
        .order_by(Ticket.priority.desc(), Ticket.created_at.desc())
        .all()
    )

    appointments = (
        Appointment.query.filter_by(client_id=client_id)
        .order_by(Appointment.start_time.asc())
        .all()
    )

    upcoming_appointments = [
        appointment
        for appointment in appointments
        if appointment.status == "scheduled" and appointment.start_time >= now
    ]

    next_appointment = upcoming_appointments[0] if upcoming_appointments else None
    client_payload["next_appointment"] = (
        next_appointment.to_dict() if next_appointment else None
    )

    incident_history = sorted(
        client.monitoring_incidents, key=lambda incident: incident.occurred_at, reverse=True
    )
    open_incidents = [incident for incident in incident_history if incident.status != "resolved"]

    metrics = {
        "total_assets": len(client.assets),
        "active_contracts": sum(
            1 for contract in client.contracts if contract.start_date <= today <= contract.end_date
        ),
        "open_tickets": sum(1 for ticket in tickets if ticket.status == "open"),
        "high_priority_open_tickets": sum(
            1 for ticket in tickets if ticket.status == "open" and ticket.priority == "high"
        ),
        "open_tasks": sum(1 for task in tasks if task.status != "completed"),
        "completed_tasks": sum(1 for task in tasks if task.status == "completed"),
        "upcoming_appointments": len(upcoming_appointments),
        "completed_appointments": sum(
            1 for appointment in appointments if appointment.status == "completed"
        ),
        "open_monitoring_incidents": len(open_incidents),
        "recent_incidents": [incident.to_dict(include_ticket=True) for incident in incident_history[:5]],
    }

    churn_probability, churn_label = calculate_churn_risk(
        client, tickets, tasks, appointments
    )
    engineer_load = calculate_engineer_load(tasks, appointments)
    client_payload["predictive_insights"] = {
        "churn_probability": round(churn_probability, 2),
        "churn_level": churn_label,
        "engineer_load": engineer_load,
    }

    return jsonify(
        {
            "client": client_payload,
            "tickets": [ticket.to_dict(include_notes=True) for ticket in tickets],
            "metrics": metrics,
            "tasks": [task.to_dict() for task in tasks],
            "appointments": [appointment.to_dict() for appointment in appointments],
        }
    )


@crm_bp.route("/clients/<int:client_id>/appointments", methods=["GET"])
def list_client_appointments(client_id: int):
    Client.query.get_or_404(client_id)
    status = request.args.get("status")
    query = Appointment.query.filter_by(client_id=client_id)
    if status:
        query = query.filter_by(status=sanitise_appointment_status(status))
    appointments = query.order_by(Appointment.start_time.asc()).all()
    return jsonify([appointment.to_dict() for appointment in appointments])


@crm_bp.route("/clients/<int:client_id>/appointments", methods=["POST"])
def create_client_appointment(client_id: int):
    Client.query.get_or_404(client_id)
    data = parse_json()

    if "start_time" not in data:
        abort(400, description="Field start_time is required")

    start_time = parse_iso_datetime(str(data["start_time"]), "start_time")

    duration_minutes = data.get("duration_minutes", 60)
    try:
        duration_minutes = int(duration_minutes)
    except (TypeError, ValueError):
        abort(400, description="duration_minutes must be an integer")
    if duration_minutes <= 0:
        abort(400, description="duration_minutes must be positive")

    appointment = Appointment(
        client_id=client_id,
        title=data.get("title", "Service visit"),
        description=data.get("description"),
        start_time=start_time,
        duration_minutes=duration_minutes,
        status=sanitise_appointment_status(data.get("status")),
        assigned_to=data.get("assigned_to"),
        location=data.get("location"),
        notes=data.get("notes"),
    )
    db.session.add(appointment)
    db.session.commit()
    return jsonify(appointment.to_dict()), 201


@crm_bp.route("/appointments", methods=["GET"])
def list_appointments():
    status = request.args.get("status")
    from_arg = request.args.get("from")
    to_arg = request.args.get("to")

    query = Appointment.query
    if status:
        query = query.filter_by(status=sanitise_appointment_status(status))
    if from_arg:
        query = query.filter(Appointment.start_time >= parse_iso_datetime(from_arg, "from"))
    if to_arg:
        query = query.filter(Appointment.start_time <= parse_iso_datetime(to_arg, "to"))

    appointments = query.order_by(Appointment.start_time.asc()).all()
    return jsonify([appointment.to_dict(include_client=True) for appointment in appointments])


@crm_bp.route("/appointments/<int:appointment_id>", methods=["PUT"])
def update_appointment(appointment_id: int):
    appointment = Appointment.query.get_or_404(appointment_id)
    data = parse_json()

    for field in ["title", "description", "assigned_to", "location", "notes"]:
        if field in data:
            setattr(appointment, field, data[field])

    if "status" in data:
        appointment.status = sanitise_appointment_status(data.get("status"))

    if "start_time" in data:
        if data["start_time"]:
            appointment.start_time = parse_iso_datetime(str(data["start_time"]), "start_time")

    if "duration_minutes" in data:
        if data["duration_minutes"] is None:
            abort(400, description="duration_minutes cannot be null")
        try:
            duration = int(data["duration_minutes"])
        except (TypeError, ValueError):
            abort(400, description="duration_minutes must be an integer")
        if duration <= 0:
            abort(400, description="duration_minutes must be positive")
        appointment.duration_minutes = duration

    db.session.commit()
    return jsonify(appointment.to_dict(include_client=True))


@crm_bp.route("/appointments/<int:appointment_id>", methods=["DELETE"])
def delete_appointment(appointment_id: int):
    appointment = Appointment.query.get_or_404(appointment_id)
    db.session.delete(appointment)
    db.session.commit()
    return "", 204


@crm_bp.route("/clients/<int:client_id>/remote-access", methods=["GET"])
def client_remote_access(client_id: int):
    """Return remote access information for the client."""

    client = Client.query.get_or_404(client_id)
    return jsonify(
        {
            "client_id": client.id,
            "remote_support_tool": client.remote_support_tool,
            "remote_desktop_id": client.remote_desktop_id,
        }
    )


@crm_bp.route("/clients/<int:client_id>/remote-access/sync", methods=["POST"])
def sync_client_remote_access(client_id: int):
    """Ensure AnyDesk is installed on the host and update the client's remote ID."""

    client = Client.query.get_or_404(client_id)
    status = ensure_anydesk()

    if not status.installed:
        return (
            jsonify(
                {
                    "client_id": client.id,
                    "remote_support_tool": status.tool,
                    "remote_desktop_id": client.remote_desktop_id,
                    "message": status.message,
                }
            ),
            503,
        )

    if status.desk_id:
        client.remote_support_tool = status.tool
        client.remote_desktop_id = status.desk_id
        db.session.commit()

    return jsonify(
        {
            "client_id": client.id,
            "remote_support_tool": status.tool,
            "remote_desktop_id": client.remote_desktop_id,
            "message": status.message,
        }
    )


@crm_bp.route("/tickets", methods=["POST"])
def create_ticket():
    data = parse_json()
    try:
        client_id = int(data["client_id"])
    except (KeyError, ValueError) as exc:
        abort(400, description=f"Invalid client id: {exc}")

    Client.query.get_or_404(client_id)
    due_date = None
    if data.get("due_date"):
        try:
            due_date = datetime.fromisoformat(data["due_date"]).date()
        except ValueError as exc:
            abort(400, description=f"Invalid due date: {exc}")

    ticket = Ticket(
        client_id=client_id,
        subject=data.get("subject", "Support request"),
        description=data.get("description"),
        priority=data.get("priority", "normal"),
        status=data.get("status", "open"),
        assigned_to=data.get("assigned_to"),
        due_date=due_date,
    )
    db.session.add(ticket)
    db.session.commit()
    return jsonify(ticket.to_dict(include_notes=True)), 201


@crm_bp.route("/tickets", methods=["GET"])
def list_tickets():
    status = request.args.get("status")
    query = Ticket.query
    if status:
        query = query.filter_by(status=status)
    tickets = query.order_by(Ticket.priority.desc(), Ticket.created_at.desc()).all()
    return jsonify([ticket.to_dict() for ticket in tickets])


@crm_bp.route("/tickets/<int:ticket_id>", methods=["PUT"])
def update_ticket(ticket_id: int):
    ticket = Ticket.query.get_or_404(ticket_id)
    data = parse_json()
    for field in ["subject", "description", "priority", "status", "assigned_to"]:
        if field in data:
            setattr(ticket, field, data[field])
    if "due_date" in data:
        if data["due_date"]:
            try:
                ticket.due_date = datetime.fromisoformat(data["due_date"]).date()
            except ValueError as exc:
                abort(400, description=f"Invalid due date: {exc}")
        else:
            ticket.due_date = None
    db.session.commit()
    return jsonify(ticket.to_dict(include_notes=True))


@crm_bp.route("/tickets/<int:ticket_id>/notes", methods=["POST"])
def create_ticket_note(ticket_id: int):
    ticket = Ticket.query.get_or_404(ticket_id)
    data = parse_json()
    note = TicketNote(
        ticket_id=ticket.id,
        author=data.get("author", "Technician"),
        body=data.get("body", ""),
    )
    db.session.add(note)
    db.session.commit()
    return jsonify(note.to_dict()), 201


@crm_bp.route("/dashboard/overview", methods=["GET"])
def dashboard_overview():
    total_clients = Client.query.count()
    open_tickets = Ticket.query.filter_by(status="open").count()
    high_priority = Ticket.query.filter_by(priority="high", status="open").count()
    active_contracts = ServiceContract.query.count()
    open_tasks = Task.query.filter(Task.status != "completed").count()
    upcoming_appointments = (
        Appointment.query.filter(
            Appointment.status == "scheduled", Appointment.start_time >= datetime.utcnow()
        ).count()
    )
    open_incidents = MonitoringIncident.query.filter(
        MonitoringIncident.status != "resolved"
    ).count()
    published_articles = KnowledgeBaseArticle.query.filter_by(is_published=True).count()

    return jsonify(
        {
            "total_clients": total_clients,
            "open_tickets": open_tickets,
            "high_priority_tickets": high_priority,
            "active_contracts": active_contracts,
            "open_tasks": open_tasks,
            "upcoming_appointments": upcoming_appointments,
            "open_monitoring_incidents": open_incidents,
            "published_knowledge_base_articles": published_articles,
        }
    )


@crm_bp.route("/analytics/forecasts", methods=["GET"])
def analytics_forecasts():
    clients = Client.query.order_by(Client.name).all()
    churn_predictions = []
    high_risk_clients = 0

    for client in clients:
        tickets = list(client.tickets)
        tasks = list(client.tasks)
        appointments = list(client.appointments)
        probability, label = calculate_churn_risk(client, tickets, tasks, appointments)
        recommendation = "Maintain regular check-ins"
        if label == "high":
            high_risk_clients += 1
            recommendation = "Escalate to account manager for recovery plan"
        elif label == "medium":
            recommendation = "Schedule proactive review within two weeks"

        churn_predictions.append(
            {
                "client_id": client.id,
                "client_name": client.name,
                "churn_probability": round(probability, 2),
                "churn_level": label,
                "open_tickets": sum(1 for ticket in tickets if ticket.status == "open"),
                "open_high_priority_tickets": sum(
                    1
                    for ticket in tickets
                    if ticket.status == "open" and ticket.priority == "high"
                ),
                "open_tasks": sum(1 for task in tasks if task.status != "completed"),
                "monitoring_incidents": sum(
                    1 for incident in client.monitoring_incidents if incident.status != "resolved"
                ),
                "recommendation": recommendation,
            }
        )

    engineer_load = calculate_engineer_load(Task.query.all(), Appointment.query.all())

    return jsonify(
        {
            "client_churn": churn_predictions,
            "engineer_load": engineer_load["engineers"],
            "summary": {
                "total_clients": len(clients),
                "high_risk_clients": high_risk_clients,
                "generated_at": datetime.utcnow().isoformat(),
            },
        }
    )


@crm_bp.route("/companies", methods=["GET"])
def list_companies():
    companies = Company.query.order_by(Company.name).all()
    return jsonify([company.to_dict() for company in companies])


@crm_bp.route("/companies", methods=["POST"])
def create_company():
    data = parse_json()
    if "name" not in data:
        abort(400, description="Company name is required")
    company = Company(
        name=data["name"],
        industry=data.get("industry"),
        headquarters=data.get("headquarters"),
    )
    db.session.add(company)
    db.session.commit()
    return jsonify(company.to_dict()), 201


@crm_bp.route("/companies/<int:company_id>", methods=["GET"])
def get_company(company_id: int):
    company = Company.query.get_or_404(company_id)
    return jsonify(company.to_dict())


@crm_bp.route("/companies/<int:company_id>", methods=["PUT"])
def update_company(company_id: int):
    company = Company.query.get_or_404(company_id)
    data = parse_json()
    for field in ["name", "industry", "headquarters"]:
        if field in data:
            setattr(company, field, data[field])
    db.session.commit()
    return jsonify(company.to_dict())


@crm_bp.route("/companies/<int:company_id>", methods=["DELETE"])
def delete_company(company_id: int):
    company = Company.query.get_or_404(company_id)
    db.session.delete(company)
    db.session.commit()
    return "", 204


@crm_bp.route("/knowledge-base", methods=["GET"])
def list_knowledge_base_articles():
    query = KnowledgeBaseArticle.query
    if request.args.get("published"):
        value = request.args["published"].lower() == "true"
        query = query.filter_by(is_published=value)
    if request.args.get("client_id"):
        try:
            client_id = int(request.args["client_id"])
        except (TypeError, ValueError):
            abort(400, description="Invalid client id filter")
        query = query.filter_by(client_id=client_id)
    articles = query.order_by(KnowledgeBaseArticle.updated_at.desc()).all()
    return jsonify([article.to_dict() for article in articles])


@crm_bp.route("/knowledge-base", methods=["POST"])
def create_knowledge_base_article():
    data = parse_json()
    if "title" not in data or "body" not in data:
        abort(400, description="Title and body are required")
    article = KnowledgeBaseArticle(
        title=data["title"],
        summary=data.get("summary"),
        body=data["body"],
        category=data.get("category"),
        tags=",".join(data.get("tags", [])) if isinstance(data.get("tags"), list) else data.get("tags"),
        author=data.get("author", "Knowledge Team"),
        is_published=bool(data.get("is_published", True)),
    )
    if data.get("client_id"):
        try:
            client_id = int(data["client_id"])
        except (TypeError, ValueError):
            abort(400, description="Invalid client id")
        Client.query.get_or_404(client_id)
        article.client_id = client_id
    db.session.add(article)
    db.session.commit()
    return jsonify(article.to_dict()), 201


@crm_bp.route("/knowledge-base/<int:article_id>", methods=["GET"])
def get_knowledge_base_article(article_id: int):
    article = KnowledgeBaseArticle.query.get_or_404(article_id)
    return jsonify(article.to_dict())


@crm_bp.route("/knowledge-base/<int:article_id>", methods=["PUT"])
def update_knowledge_base_article(article_id: int):
    article = KnowledgeBaseArticle.query.get_or_404(article_id)
    data = parse_json()
    for field in ["title", "summary", "body", "category", "author"]:
        if field in data:
            setattr(article, field, data[field])
    if "tags" in data:
        article.tags = (
            ",".join(data["tags"]) if isinstance(data["tags"], list) else data["tags"]
        )
    if "is_published" in data:
        article.is_published = bool(data["is_published"])
    if "client_id" in data:
        if data["client_id"] is None:
            article.client_id = None
        else:
            try:
                client_id = int(data["client_id"])
            except (TypeError, ValueError):
                abort(400, description="Invalid client id")
            Client.query.get_or_404(client_id)
            article.client_id = client_id
    db.session.commit()
    return jsonify(article.to_dict())


@crm_bp.route("/knowledge-base/<int:article_id>", methods=["DELETE"])
def delete_knowledge_base_article(article_id: int):
    article = KnowledgeBaseArticle.query.get_or_404(article_id)
    db.session.delete(article)
    db.session.commit()
    return "", 204


@crm_bp.route("/clients/<int:client_id>/tasks", methods=["GET"])
def list_client_tasks(client_id: int):
    Client.query.get_or_404(client_id)
    tasks = (
        Task.query.filter_by(client_id=client_id)
        .order_by(Task.due_date, Task.priority.desc(), Task.created_at.desc())
        .all()
    )
    return jsonify([task.to_dict() for task in tasks])


@crm_bp.route("/clients/<int:client_id>/tasks", methods=["POST"])
def create_client_task(client_id: int):
    Client.query.get_or_404(client_id)
    data = parse_json()
    if "title" not in data:
        abort(400, description="Task title is required")
    due_date = None
    if data.get("due_date"):
        try:
            due_date = datetime.fromisoformat(data["due_date"]).date()
        except ValueError as exc:
            abort(400, description=f"Invalid task due date: {exc}")
    task = Task(
        client_id=client_id,
        title=data["title"],
        description=data.get("description"),
        status=data.get("status", "pending"),
        priority=data.get("priority", "normal"),
        due_date=due_date,
        assigned_to=data.get("assigned_to"),
        created_by=data.get("created_by"),
    )
    db.session.add(task)
    db.session.commit()
    return jsonify(task.to_dict()), 201


@crm_bp.route("/tasks", methods=["GET"])
def list_tasks():
    status = request.args.get("status")
    query = Task.query
    if status:
        query = query.filter_by(status=status)
    tasks = query.order_by(Task.due_date, Task.created_at.desc()).all()
    return jsonify([task.to_dict() for task in tasks])


@crm_bp.route("/tasks/<int:task_id>", methods=["PUT"])
def update_task(task_id: int):
    task = Task.query.get_or_404(task_id)
    data = parse_json()
    for field in ["title", "description", "status", "priority", "assigned_to", "created_by"]:
        if field in data:
            setattr(task, field, data[field])
    if "due_date" in data:
        if data["due_date"]:
            try:
                task.due_date = datetime.fromisoformat(data["due_date"]).date()
            except ValueError as exc:
                abort(400, description=f"Invalid task due date: {exc}")
        else:
            task.due_date = None
    if "client_id" in data:
        if data["client_id"] is None:
            task.client = None
        else:
            try:
                new_client_id = int(data["client_id"])
            except (TypeError, ValueError):
                abort(400, description="Invalid client id")
            Client.query.get_or_404(new_client_id)
            task.client_id = new_client_id
    db.session.commit()
    return jsonify(task.to_dict())


@crm_bp.route("/tasks/<int:task_id>/complete", methods=["POST"])
def complete_task(task_id: int):
    task = Task.query.get_or_404(task_id)
    task.status = "completed"
    task.completed_at = datetime.utcnow()
    db.session.commit()
    return jsonify(task.to_dict())


@crm_bp.route("/schedule/optimize", methods=["POST"])
def optimise_schedule():
    data = parse_json()
    if "date" not in data:
        abort(400, description="A target date is required")

    try:
        target_date = datetime.fromisoformat(data["date"]).date()
    except ValueError as exc:
        abort(400, description=f"Invalid date: {exc}")

    travel_buffer = int(data.get("travel_buffer_minutes", 30))
    workday_minutes = int(data.get("workday_minutes", 8 * 60))
    engineers_input = data.get("engineers") or []

    day_start = datetime.combine(target_date, time(hour=9, minute=0))
    day_end = day_start + timedelta(minutes=workday_minutes)

    engineer_slots: Dict[str, datetime] = {}
    for engineer in engineers_input:
        engineer_slots[str(engineer)] = day_start

    start_window = datetime.combine(target_date, time.min)
    end_window = datetime.combine(target_date, time.max)

    appointments = (
        Appointment.query.filter(
            Appointment.status == "scheduled",
            Appointment.start_time >= start_window,
            Appointment.start_time <= end_window,
        )
        .order_by(Appointment.start_time.asc())
        .all()
    )

    suggestions = []
    reassigned = 0

    for appointment in appointments:
        engineer = appointment.assigned_to or None
        if engineer and engineer not in engineer_slots:
            engineer_slots[engineer] = day_start

        if not engineer:
            if engineer_slots:
                engineer = min(engineer_slots, key=engineer_slots.get)
            else:
                engineer = "Unassigned"
                engineer_slots[engineer] = day_start
            reassigned += 1

        suggested_start = max(appointment.start_time, engineer_slots[engineer])
        reason = None
        if suggested_start > appointment.start_time:
            reason = "Resolved overlap with preceding visit"

        suggested_end = suggested_start + timedelta(minutes=appointment.duration_minutes)
        if suggested_end + timedelta(minutes=travel_buffer) > day_end:
            reason = "Extends beyond workday; consider another engineer"

        engineer_slots[engineer] = suggested_end + timedelta(minutes=travel_buffer)

        suggestions.append(
            {
                "appointment_id": appointment.id,
                "client": appointment.client.name if appointment.client else None,
                "assigned_engineer": appointment.assigned_to,
                "recommended_engineer": engineer,
                "current_start": appointment.start_time.isoformat(),
                "optimized_start": suggested_start.isoformat(),
                "optimized_end": suggested_end.isoformat(),
                "travel_buffer_minutes": travel_buffer,
                "notes": reason,
            }
        )

    return jsonify(
        {
            "date": target_date.isoformat(),
            "appointments_considered": len(appointments),
            "reassignments": reassigned,
            "suggestions": suggestions,
            "generated_at": datetime.utcnow().isoformat(),
        }
    )


@crm_bp.route("/clients/<int:client_id>/theme", methods=["PUT"])
def update_client_theme(client_id: int):
    client = Client.query.get_or_404(client_id)
    data = parse_json()
    client.theme_preference = sanitise_theme(data.get("theme"))
    db.session.commit()
    return jsonify({"client_id": client.id, "theme": client.theme_preference})


@crm_bp.route("/users/<int:user_id>/theme", methods=["PUT"])
def update_user_theme(user_id: int):
    user = require_user(user_id)
    data = parse_json()
    user.theme_preference = sanitise_theme(data.get("theme"))
    db.session.commit()
    return jsonify({"user_id": user.id, "theme": user.theme_preference})


@crm_bp.route("/auth/register", methods=["POST"])
def register_user():
    data = parse_json()
    for field in ["username", "email", "password"]:
        if field not in data:
            abort(400, description="Missing required user field")
    if User.query.filter_by(username=data["username"]).first():
        abort(409, description="Username already exists")
    if User.query.filter_by(email=data["email"]).first():
        abort(409, description="Email already registered")
    user = User(
        username=data["username"],
        email=data["email"],
        password_hash=generate_password_hash(data["password"]),
        theme_preference=sanitise_theme(data.get("theme_preference")),
    )
    db.session.add(user)
    db.session.commit()
    return jsonify(user.to_dict()), 201


@crm_bp.route("/auth/login", methods=["POST"])
def login_user():
    data = parse_json()
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        abort(400, description="Username and password are required")
    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        abort(401, description="Invalid credentials")
    if user.two_factor_enabled:
        otp = data.get("otp")
        if not otp:
            return (
                jsonify(
                    {
                        "two_factor_required": True,
                        "message": "Two-factor authentication code required",
                        "user_id": user.id,
                    }
                ),
                202,
            )
        totp = pyotp.TOTP(user.two_factor_secret)
        if not totp.verify(str(otp)):
            abort(401, description="Invalid authentication code")
    return jsonify({"message": "Login successful", "user": user.to_dict()})


@crm_bp.route("/auth/two-factor/setup", methods=["POST"])
def setup_two_factor():
    data = parse_json()
    user_id = data.get("user_id")
    password = data.get("password")
    if user_id is None or password is None:
        abort(400, description="User id and password are required")
    user = require_user(int(user_id))
    if not check_password_hash(user.password_hash, password):
        abort(401, description="Invalid credentials")
    secret = pyotp.random_base32()
    user.two_factor_secret = secret
    user.two_factor_enabled = False
    db.session.commit()
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name=user.email, issuer_name="CRM Desk")
    return jsonify({"secret": secret, "provisioning_uri": provisioning_uri})


@crm_bp.route("/auth/two-factor/verify", methods=["POST"])
def verify_two_factor():
    data = parse_json()
    user_id = data.get("user_id")
    otp = data.get("otp")
    if user_id is None or otp is None:
        abort(400, description="User id and otp are required")
    user = require_user(int(user_id))
    if not user.two_factor_secret:
        abort(400, description="Two-factor setup not initiated")
    totp = pyotp.TOTP(user.two_factor_secret)
    if not totp.verify(str(otp)):
        abort(401, description="Invalid authentication code")
    user.two_factor_enabled = True
    db.session.commit()
    return jsonify({"message": "Two-factor authentication enabled", "user": user.to_dict()})


@crm_bp.route("/auth/two-factor/disable", methods=["POST"])
def disable_two_factor():
    data = parse_json()
    user_id = data.get("user_id")
    password = data.get("password")
    if user_id is None or password is None:
        abort(400, description="User id and password are required")
    user = require_user(int(user_id))
    if not check_password_hash(user.password_hash, password):
        abort(401, description="Invalid credentials")
    user.two_factor_enabled = False
    user.two_factor_secret = None
    db.session.commit()
    return jsonify({"message": "Two-factor authentication disabled", "user": user.to_dict()})
