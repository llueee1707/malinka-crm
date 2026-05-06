from __future__ import annotations

from difflib import SequenceMatcher
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import Query
from fastapi import Request
from fastapi import UploadFile
from fastapi import status
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Select
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload
from sqlalchemy.orm import selectinload

from app.core.config import BASE_DIR
from app.core.config import get_settings
from app.core.security import create_access_token
from app.core.security import decode_access_token
from app.core.security import get_password_hash
from app.core.security import verify_password
from app.db.session import get_db
from app.models.entities import Appointment
from app.models.entities import AppointmentReceiptPhoto
from app.models.entities import AppointmentStatus
from app.models.entities import BookingRequest
from app.models.entities import BookingRequestStatus
from app.models.entities import Client
from app.models.entities import MasterAvailability
from app.models.entities import MasterServicePrice
from app.models.entities import Service
from app.models.entities import Setting
from app.models.entities import User
from app.models.entities import UserRole
from app.models.entities import utc_now
from app.services.salon import PriceSummary
from app.services.salon import appointment_filter_label
from app.services.salon import appointment_filter_options
from app.services.salon import appointment_status_label
from app.services.salon import appointment_visual_status
from app.services.salon import appointment_visual_status_label
from app.services.salon import calculate_appointment_price_map
from app.services.salon import apply_period_scope
from app.services.salon import apply_master_scope
from app.services.salon import calculate_master_prices
from app.services.salon import enforce_working_hours
from app.services.salon import get_visible_masters
from app.services.salon import initials
from app.services.salon import is_manager
from app.services.salon import month_label
from app.services.salon import month_bounds
from app.services.salon import month_weeks
from app.services.salon import parse_day
from app.services.salon import parse_appointment_filter
from app.services.salon import parse_month
from app.services.salon import resolve_master_filter
from app.services.salon import role_label
from app.services.salon import status_label
from app.services.salon import sync_master_statuses
from app.services.salon import today_range
from app.services.uploads import save_avatar
from app.services.uploads import save_receipt_photo


router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
templates.env.globals.update(
    role_label=role_label,
    status_label=status_label,
    initials=initials,
    month_label=month_label,
    appointment_status_label=appointment_status_label,
    appointment_visual_status=appointment_visual_status,
    appointment_visual_status_label=appointment_visual_status_label,
)

SERVICE_ICON_CHOICES = [
    "✂",
    "🎨",
    "💅",
    "✨",
    "🦶",
    "💄",
    "🫧",
    "🪒",
]
DEFAULT_SERVICE_ICON = SERVICE_ICON_CHOICES[0]
REPORT_CHART_COLORS = [
    "#b877ff",
    "#ff93c9",
    "#71d59c",
    "#7bc8ff",
    "#ffc46b",
    "#8f8cff",
]

_RU_EN_LAYOUT_MAP = str.maketrans(
    "ёйцукенгшщзхъфывапролджэячсмитьбю",
    "`qwertyuiop[]asdfghjkl;'zxcvbnm,.",
)
_EN_RU_LAYOUT_MAP = str.maketrans(
    "`qwertyuiop[]asdfghjkl;'zxcvbnm,.",
    "ёйцукенгшщзхъфывапролджэячсмитьбю",
)


def format_won(value: int | float | str | None) -> str:
    if value is None:
        return "0 ₩"
    try:
        amount = int(value)
    except (TypeError, ValueError):
        amount = 0
    return f"{amount:,}".replace(",", " ") + " ₩"


templates.env.globals.update(format_won=format_won)


def redirect_to(path: str, **params: object) -> RedirectResponse:
    filtered = {key: value for key, value in params.items() if value not in (None, "", [])}
    query = urlencode(filtered, doseq=True)
    url = f"{path}?{query}" if query else path
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


def page(
    request: Request,
    template_name: str,
    user: User | None,
    context: dict[str, object] | None = None,
    *,
    status_code: int = status.HTTP_200_OK,
    db: Session | None = None,
) -> object:
    current_path = request.url.path
    sidebar_nav = build_sidebar_nav(user)
    for item in sidebar_nav:
        item["is_active"] = any(current_path.startswith(prefix) for prefix in item.get("match", []))

    new_requests_count = 0
    if user is not None and is_manager(user) and db is not None:
        new_requests_count = db.scalar(
            select(func.count(BookingRequest.id)).where(
                BookingRequest.status == BookingRequestStatus.NEW
            )
        ) or 0
        for item in sidebar_nav:
            if item.get("href") == "/booking-requests":
                item["badge"] = new_requests_count

    payload = {
        "request": request,
        "current_user": user,
        "notice": request.query_params.get("notice"),
        "error_message": request.query_params.get("error"),
        "current_path": current_path,
        "sidebar_nav": sidebar_nav,
        "new_booking_requests_count": new_requests_count,
    }
    if context:
        payload.update(context)
    return templates.TemplateResponse(template_name, payload, status_code=status_code)


def to_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    clean = value.strip()
    if not clean:
        return None
    return int(clean)


def parse_optional_int_query(value: str | None) -> int | None:
    try:
        return to_optional_int(value)
    except ValueError:
        return None


def parse_optional_date_query(value: str | None) -> date | None:
    if value is None:
        return None
    clean = value.strip()
    if not clean:
        return None
    try:
        return datetime.strptime(clean, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_reminder_offsets(raw: str | None) -> list[int]:
    if not raw:
        return []
    result: list[int] = []
    seen: set[int] = set()
    for chunk in raw.split(","):
        clean = chunk.strip()
        if not clean or not clean.isdigit():
            continue
        value = int(clean)
        if value < 1 or value > 1440 or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return sorted(result, reverse=True)


def normalize_search_text(value: str | None) -> str:
    if not value:
        return ""
    return "".join(char for char in value.casefold().strip() if char.isalnum())


def build_search_variants(value: str) -> set[str]:
    normalized = normalize_search_text(value)
    if not normalized:
        return set()

    variants = {normalized}
    variants.add(normalize_search_text(normalized.translate(_RU_EN_LAYOUT_MAP)))
    variants.add(normalize_search_text(normalized.translate(_EN_RU_LAYOUT_MAP)))
    return {item for item in variants if item}


def is_close_match(query: str, candidate: str) -> bool:
    if not query or not candidate:
        return False
    if query in candidate:
        return True
    if candidate.startswith(query):
        return True
    if len(query) < 3:
        return False
    if abs(len(candidate) - len(query)) > 2:
        return False
    return SequenceMatcher(None, query, candidate).ratio() >= 0.75


def client_matches_search(client: Client, raw_query: str) -> bool:
    query_variants = build_search_variants(raw_query)
    if not query_variants:
        return True

    normalized_name = normalize_search_text(client.full_name)
    searchable_fields = [
        normalized_name,
        normalize_search_text(client.phone),
        normalize_search_text(client.instagram),
    ]
    name_tokens = [normalize_search_text(token) for token in (client.full_name or "").split()]
    name_tokens = [token for token in name_tokens if token]

    for variant in query_variants:
        if any(variant in field for field in searchable_fields if field):
            return True

        if any(is_close_match(variant, token) for token in name_tokens):
            return True
        if is_close_match(variant, normalized_name):
            return True

    return False


def normalize_reminder_offsets(values: list[str]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for raw in values:
        clean = str(raw).strip()
        if not clean:
            continue
        if not clean.isdigit():
            raise ValueError("Интервалы напоминаний должны быть целыми числами.")
        value = int(clean)
        if value < 1 or value > 1440:
            raise ValueError("Интервалы напоминаний должны быть от 1 до 1440 минут.")
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return sorted(result, reverse=True)


def get_current_user(request: Request, db: Session) -> User | None:
    settings = get_settings()
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    username = payload.get("sub")
    if not username:
        return None
    return db.scalar(select(User).where(User.username == username))


def ensure_authenticated(request: Request, db: Session) -> User | RedirectResponse:
    user = get_current_user(request, db)
    if not user:
        return redirect_to("/login")
    sync_master_statuses(db)
    db.refresh(user)
    return user


def ensure_manager_access(user: User) -> RedirectResponse | None:
    if not is_manager(user):
        return redirect_to("/dashboard", error="Доступ разрешен только директору и администратору.")
    return None


def ensure_appointment_create_access(user: User) -> RedirectResponse | None:
    if is_manager(user) or user.role == UserRole.MASTER:
        return None
    return redirect_to("/dashboard", error="Недостаточно прав для создания записей.")


def ensure_director_access(user: User) -> RedirectResponse | None:
    if not is_manager(user):
        return redirect_to("/dashboard", error="Доступ разрешен только директору и администратору.")
    return None


def build_sidebar_nav(user: User | None) -> list[dict[str, object]]:
    if not user:
        return []

    items: list[dict[str, object]] = []

    items.append({"href": "/dashboard", "label": "Обзор", "match": ["/dashboard"]})

    if user.role in {UserRole.DIRECTOR, UserRole.ADMIN}:
        items.append({"href": "/reports", "label": "Отчетность", "match": ["/reports"]})
        items.append({"href": "/booking-requests", "label": "Заявки", "match": ["/booking-requests"]})

    items.extend(
        [
            {"href": "/appointments", "label": "Записи", "match": ["/appointments"]},
            {"href": "/calendar", "label": "Календарь", "match": ["/calendar"]},
        ]
    )

    if user.role in {UserRole.DIRECTOR, UserRole.ADMIN, UserRole.MASTER}:
        items.append({"href": "/clients", "label": "Клиенты", "match": ["/clients"]})

    if user.role in {UserRole.DIRECTOR, UserRole.ADMIN}:
        items.append({"href": "/team", "label": "Команда", "match": ["/team"]})

    if user.role in {UserRole.DIRECTOR, UserRole.ADMIN, UserRole.MASTER}:
        items.append({"href": "/settings", "label": "Настройки", "match": ["/settings"]})

    return items

    items: list[dict[str, object]] = []

    items.append({"href": "/dashboard", "label": "Отчеты", "match": ["/dashboard"]})

    items.extend(
        [
            {"href": "/appointments", "label": "Записи", "match": ["/appointments"]},
            {"href": "/calendar", "label": "Календарь", "match": ["/calendar"]},
        ]
    )

    if user.role in {UserRole.DIRECTOR, UserRole.ADMIN, UserRole.MASTER}:
        items.extend(
            [
                {"href": "/clients", "label": "Клиенты", "match": ["/clients"]},
            ]
        )

    if user.role in {UserRole.DIRECTOR, UserRole.ADMIN}:
        items.append({"href": "/team", "label": "Команда", "match": ["/team"]})

    if user.role in {UserRole.DIRECTOR, UserRole.ADMIN, UserRole.MASTER}:
        items.append({"href": "/settings", "label": "Настройки", "match": ["/settings"]})

    return items


def appointment_base_query() -> Select:
    return (
        select(Appointment)
        .options(
            joinedload(Appointment.client),
            joinedload(Appointment.master),
            selectinload(Appointment.services),
            selectinload(Appointment.receipt_photos),
        )
        .order_by(Appointment.starts_at.asc())
    )


def build_appointments_board_context(
    db: Session,
    user: User,
    *,
    section_value: str | None,
    period_value: str | None,
    selected_master_id: int | None = None,
    selected_service_id: int | None = None,
    create_form_data: dict[str, object] | None = None,
    create_form_error: str | None = None,
    create_modal_open: bool = False,
) -> dict[str, object]:
    active_section = section_value if section_value in {"pending", "completed"} else "pending"
    active_period = parse_appointment_filter(period_value)
    filter_options = appointment_filter_options()
    visible_masters = get_visible_masters(db, user)
    selected_master = resolve_master_filter(db, user, selected_master_id)
    services = list(db.scalars(select(Service).order_by(Service.name.asc())))
    selected_service = db.get(Service, selected_service_id) if selected_service_id else None

    pending_query = appointment_base_query().where(Appointment.status == AppointmentStatus.PENDING)
    pending_query = apply_master_scope(pending_query, user, selected_master)
    if selected_service:
        pending_query = pending_query.where(Appointment.services.any(Service.id == selected_service.id))
    pending_query = apply_period_scope(pending_query, Appointment.starts_at, active_period)
    pending_appointments = list(db.scalars(pending_query).unique())

    completed_query = appointment_base_query().where(
        Appointment.status.in_([AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED])
    )
    completed_query = apply_master_scope(completed_query, user, selected_master)
    if selected_service:
        completed_query = completed_query.where(Appointment.services.any(Service.id == selected_service.id))
    completed_query = apply_period_scope(completed_query, Appointment.starts_at, active_period)
    completed_query = completed_query.order_by(None).order_by(Appointment.starts_at.desc())
    completed_appointments = list(db.scalars(completed_query).unique())

    is_pending_page = active_section == "pending"
    visible_appointments = pending_appointments if is_pending_page else completed_appointments
    appointment_price_map = calculate_appointment_price_map(db, visible_appointments)

    context = {
        "appointments_page_kind": active_section,
        "appointments": visible_appointments,
        "appointments_page_title": "Ожидающие" if is_pending_page else "Выполненные",
        "appointments_page_lead": (
            "Будущие и уже начавшиеся записи, которые требуют внимания прямо сейчас."
            if is_pending_page
            else "История завершенных и отмененных визитов по мастерам, услугам и клиентам."
        ),
        "appointments_empty_title": "Нет ожидающих записей" if is_pending_page else "Нет выполненных записей",
        "appointments_empty_text": (
            "По выбранному периоду нет будущих или активных визитов."
            if is_pending_page
            else "По выбранному периоду нет выполненных или отмененных записей."
        ),
        "active_period": active_period,
        "active_period_label": appointment_filter_label(active_period),
        "filter_options": filter_options,
        "appointment_price_map": appointment_price_map,
        "pending_count": len(pending_appointments),
        "completed_count": len(completed_appointments),
        "can_manage_appointments": is_manager(user),
        "show_create_card": user.role == UserRole.MASTER and is_pending_page,
        "show_actions": is_pending_page,
        "section_path": "/appointments",
        "create_modal_open": create_modal_open,
        "is_calendar_day_view": False,
        "visible_masters": visible_masters,
        "selected_master": selected_master,
        "services": services,
        "selected_service": selected_service,
        "selected_master_id": selected_master.id if selected_master else None,
        "selected_service_id": selected_service.id if selected_service else None,
    }

    if user.role == UserRole.MASTER:
        context.update(
            build_appointment_form_payload(
                db,
                user,
                selected_master_id=user.id,
                form_data=create_form_data,
                error_message=create_form_error,
            )
        )

    return context


def can_view_client(user: User, client_id: int, db: Session) -> bool:
    if is_manager(user):
        return True
    if user.role != UserRole.MASTER:
        return False
    return bool(
        db.scalar(
            select(func.count(Appointment.id)).where(
                Appointment.client_id == client_id,
                Appointment.master_id == user.id,
            )
        )
    )


def build_dashboard_context(db: Session, user: User, selected_master_id: int | None) -> dict[str, object]:
    visible_masters = get_visible_masters(db, user)
    selected_master = resolve_master_filter(db, user, selected_master_id)
    today_start, today_end = today_range()

    today_query = appointment_base_query().where(
        Appointment.starts_at >= today_start,
        Appointment.starts_at <= today_end,
    )
    today_query = apply_master_scope(today_query, user, selected_master)
    today_appointments = list(db.scalars(today_query).unique())

    week_end = today_start + timedelta(days=7)
    week_query = select(func.count(Appointment.id)).where(
        Appointment.starts_at >= today_start,
        Appointment.starts_at < week_end,
    )
    week_query = apply_master_scope(week_query, user, selected_master)
    week_count = db.scalar(week_query) or 0

    busy_count = db.scalar(
        select(func.count(User.id)).where(
            User.role == UserRole.MASTER,
            User.availability_status == MasterAvailability.BUSY,
        )
    ) or 0

    return {
        "visible_masters": visible_masters,
        "selected_master": selected_master,
        "today_appointments": today_appointments,
        "today_count": len(today_appointments),
        "week_count": week_count,
        "busy_count": busy_count,
    }


def normalize_report_date_range(date_from_value: str | None, date_to_value: str | None) -> tuple[date, date]:
    today = date.today()
    default_start = today.replace(day=1)
    date_from = parse_optional_date_query(date_from_value) or default_start
    date_to = parse_optional_date_query(date_to_value) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    return date_from, date_to


def appointment_reported_at(appointment: Appointment) -> datetime:
    return appointment.completed_at or appointment.starts_at


def build_report_gradient(rows: list[dict[str, object]]) -> str:
    total = sum(int(row.get("revenue", 0) or 0) for row in rows)
    if total <= 0:
        return "conic-gradient(rgba(255, 255, 255, 0.08) 0 100%)"

    start = 0.0
    segments: list[str] = []
    for index, row in enumerate(rows):
        revenue = int(row.get("revenue", 0) or 0)
        if revenue <= 0:
            row["color"] = REPORT_CHART_COLORS[index % len(REPORT_CHART_COLORS)]
            row["share_percent"] = 0.0
            continue
        share = revenue / total * 100
        finish = min(start + share, 100.0)
        color = REPORT_CHART_COLORS[index % len(REPORT_CHART_COLORS)]
        row["color"] = color
        row["share_percent"] = round(share, 1)
        segments.append(f"{color} {start:.2f}% {finish:.2f}%")
        start = finish

    if start < 100.0:
        segments.append(f"rgba(255, 255, 255, 0.08) {start:.2f}% 100%")
    return "conic-gradient(" + ", ".join(segments) + ")"


def build_reports_context(
    db: Session,
    user: User,
    *,
    date_from_value: str | None,
    date_to_value: str | None,
    selected_master_id: int | None,
    selected_service_id: int | None,
) -> dict[str, object]:
    visible_masters = get_visible_masters(db, user)
    selected_master = resolve_master_filter(db, user, selected_master_id)
    services = list(db.scalars(select(Service).order_by(Service.name.asc())))
    service_icon_map = {service.id: service.icon or DEFAULT_SERVICE_ICON for service in services}
    selected_service = db.get(Service, selected_service_id) if selected_service_id else None

    report_date_from, report_date_to = normalize_report_date_range(date_from_value, date_to_value)
    range_start = datetime.combine(report_date_from, time.min)
    range_end = datetime.combine(report_date_to + timedelta(days=1), time.min)
    report_timestamp = func.coalesce(Appointment.completed_at, Appointment.starts_at)

    report_query = (
        select(Appointment)
        .options(
            joinedload(Appointment.master),
            selectinload(Appointment.services),
        )
        .where(
            Appointment.status == AppointmentStatus.COMPLETED,
            report_timestamp >= range_start,
            report_timestamp < range_end,
        )
        .order_by(report_timestamp.asc())
    )
    report_query = apply_master_scope(report_query, user, selected_master)
    if selected_service:
        report_query = report_query.where(Appointment.services.any(Service.id == selected_service.id))
    completed_appointments = list(db.scalars(report_query).unique())
    appointment_price_map = calculate_appointment_price_map(db, completed_appointments)

    report_days = (report_date_to - report_date_from).days + 1
    revenue_by_day = {
        report_date_from + timedelta(days=offset): 0
        for offset in range(report_days)
    }
    appointments_by_day = {
        report_date_from + timedelta(days=offset): 0
        for offset in range(report_days)
    }

    if selected_master:
        master_seed = [selected_master]
    else:
        master_seed = visible_masters

    master_rows_map: dict[int, dict[str, object]] = {
        master.id: {
            "id": master.id,
            "name": master.full_name,
            "revenue": 0,
            "appointments": 0,
            "average_ticket": 0,
            "share_percent": 0.0,
            "width_percent": 0.0,
            "top_service_label": "—",
            "service_totals": {},
            "missing_prices_count": 0,
        }
        for master in master_seed
    }

    service_rows_map: dict[int, dict[str, object]] = {}
    if selected_service:
        service_rows_map[selected_service.id] = {
            "id": selected_service.id,
            "name": selected_service.name,
            "icon": selected_service.icon or DEFAULT_SERVICE_ICON,
            "revenue": 0,
            "appointments": 0,
            "share_percent": 0.0,
            "color": REPORT_CHART_COLORS[0],
        }

    total_revenue = 0
    missing_prices_count = 0

    for appointment in completed_appointments:
        summary = appointment_price_map.get(appointment.id)
        if not summary:
            continue

        priced_items = summary.items
        missing_price = bool(summary.missing_service_names)
        if selected_service:
            priced_items = [item for item in summary.items if item["service_id"] == selected_service.id]
            missing_price = selected_service.name in summary.missing_service_names

        appointment_revenue = sum(int(item["price"]) for item in priced_items)
        reported_day = appointment_reported_at(appointment).date()
        if reported_day in revenue_by_day:
            revenue_by_day[reported_day] += appointment_revenue
            appointments_by_day[reported_day] += 1

        total_revenue += appointment_revenue
        master_row = master_rows_map.setdefault(
            appointment.master_id,
            {
                "id": appointment.master_id,
                "name": appointment.master.full_name,
                "revenue": 0,
                "appointments": 0,
                "average_ticket": 0,
                "share_percent": 0.0,
                "width_percent": 0.0,
                "top_service_label": "—",
                "service_totals": {},
                "missing_prices_count": 0,
            },
        )
        master_row["revenue"] += appointment_revenue
        master_row["appointments"] += 1

        if missing_price:
            missing_prices_count += 1
            master_row["missing_prices_count"] += 1

        for item in priced_items:
            service_id = int(item["service_id"])
            service_row = service_rows_map.setdefault(
                service_id,
                {
                    "id": service_id,
                    "name": str(item["service_name"]),
                    "icon": service_icon_map.get(service_id, DEFAULT_SERVICE_ICON),
                    "revenue": 0,
                    "appointments": 0,
                    "share_percent": 0.0,
                    "color": REPORT_CHART_COLORS[len(service_rows_map) % len(REPORT_CHART_COLORS)],
                },
            )
            service_row["revenue"] += int(item["price"])
            service_row["appointments"] += 1
            service_totals = master_row["service_totals"]
            service_totals[service_id] = {
                "name": str(item["service_name"]),
                "revenue": service_totals.get(service_id, {}).get("revenue", 0) + int(item["price"]),
            }

    master_rows = sorted(
        master_rows_map.values(),
        key=lambda row: (-int(row["revenue"]), -int(row["appointments"]), str(row["name"])),
    )
    service_rows = sorted(
        service_rows_map.values(),
        key=lambda row: (-int(row["revenue"]), -int(row["appointments"]), str(row["name"])),
    )

    max_master_revenue = max((int(row["revenue"]) for row in master_rows), default=0)
    for row in master_rows:
        appointments_count = int(row["appointments"])
        revenue = int(row["revenue"])
        row["average_ticket"] = round(revenue / appointments_count) if appointments_count else 0
        row["width_percent"] = round((revenue / max_master_revenue) * 100, 1) if max_master_revenue else 0
        row["share_percent"] = round((revenue / total_revenue) * 100, 1) if total_revenue else 0
        service_totals = row["service_totals"]
        if service_totals:
            top_service = max(service_totals.values(), key=lambda item: int(item["revenue"]))
            row["top_service_label"] = str(top_service["name"])

    max_service_revenue = max((int(row["revenue"]) for row in service_rows), default=0)
    for row in service_rows:
        revenue = int(row["revenue"])
        row["width_percent"] = round((revenue / max_service_revenue) * 100, 1) if max_service_revenue else 0
        row["share_percent"] = round((revenue / total_revenue) * 100, 1) if total_revenue else 0

    daily_chart = []
    max_daily_revenue = max(revenue_by_day.values(), default=0)
    for current_day, revenue in revenue_by_day.items():
        daily_chart.append(
            {
                "iso": current_day.isoformat(),
                "label": current_day.strftime("%d.%m"),
                "full_label": current_day.strftime("%d.%m.%Y"),
                "revenue": revenue,
                "appointments": appointments_by_day[current_day],
                "height_percent": round((revenue / max_daily_revenue) * 100, 1) if max_daily_revenue else 0,
            }
        )

    best_day = max(daily_chart, key=lambda row: int(row["revenue"])) if completed_appointments else None
    average_ticket = round(total_revenue / len(completed_appointments)) if completed_appointments else 0
    average_daily_revenue = round(total_revenue / report_days) if report_days else 0
    active_masters_count = sum(1 for row in master_rows if int(row["appointments"]) > 0)
    service_chart_gradient = build_report_gradient(service_rows) if service_rows else build_report_gradient([])

    return {
        "visible_masters": visible_masters,
        "selected_master": selected_master,
        "services": services,
        "selected_service": selected_service,
        "selected_master_id": selected_master.id if selected_master else None,
        "selected_service_id": selected_service.id if selected_service else None,
        "report_date_from": report_date_from,
        "report_date_to": report_date_to,
        "report_period_label": f"{report_date_from.strftime('%d.%m.%Y')} — {report_date_to.strftime('%d.%m.%Y')}",
        "report_period_days": report_days,
        "report_total_revenue": total_revenue,
        "report_completed_count": len(completed_appointments),
        "report_average_ticket": average_ticket,
        "report_average_daily_revenue": average_daily_revenue,
        "report_active_masters_count": active_masters_count,
        "report_missing_prices_count": missing_prices_count,
        "report_best_day": best_day,
        "daily_chart": daily_chart,
        "master_rows": master_rows,
        "service_rows": service_rows,
        "service_chart_gradient": service_chart_gradient,
    }


def build_calendar_context(
    db: Session,
    user: User,
    month_value: str | None,
    selected_master_id: int | None,
) -> dict[str, object]:
    visible_masters = get_visible_masters(db, user)
    selected_master = resolve_master_filter(db, user, selected_master_id)
    month_start = parse_month(month_value)
    range_start, range_end = month_bounds(month_start)

    query = appointment_base_query().where(
        Appointment.starts_at >= range_start,
        Appointment.starts_at < range_end,
        Appointment.status == AppointmentStatus.PENDING,
    )
    query = apply_master_scope(query, user, selected_master)
    appointments = list(db.scalars(query).unique())

    appointments_by_day = {}
    for appointment in appointments:
        appointments_by_day.setdefault(appointment.starts_at.date(), []).append(appointment)

    prev_month = (month_start - timedelta(days=1)).replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)

    return {
        "month_start": month_start,
        "weeks": month_weeks(month_start, appointments_by_day),
        "today_date": date.today(),
        "visible_masters": visible_masters,
        "selected_master": selected_master,
        "prev_month": prev_month,
        "next_month": next_month,
    }


def build_calendar_day_appointments_context(
    db: Session,
    user: User,
    selected_day: date,
    selected_master: User | None,
) -> dict[str, object]:
    range_start = datetime.combine(selected_day, time.min)
    range_end = datetime.combine(selected_day, time.max)

    pending_query = appointment_base_query().where(
        Appointment.starts_at >= range_start,
        Appointment.starts_at <= range_end,
        Appointment.status == AppointmentStatus.PENDING,
    )
    pending_query = apply_master_scope(pending_query, user, selected_master)
    pending_appointments = list(db.scalars(pending_query).unique())

    completed_count_query = select(func.count(Appointment.id)).where(
        Appointment.starts_at >= range_start,
        Appointment.starts_at <= range_end,
        Appointment.status.in_([AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED]),
    )
    completed_count_query = apply_master_scope(completed_count_query, user, selected_master)
    completed_count = db.scalar(completed_count_query) or 0

    return {
        "appointments_page_kind": "pending",
        "appointments": pending_appointments,
        "appointments_page_title": f"Ожидающие на {selected_day.strftime('%d.%m.%Y')}",
        "appointments_page_lead": "Список ожидающих записей только за выбранный день.",
        "appointments_empty_title": "Нет ожидающих записей на выбранный день",
        "appointments_empty_text": "На эту дату нет активных записей по выбранному фильтру мастера.",
        "active_period": "day",
        "active_period_label": selected_day.strftime("%d.%m.%Y"),
        "filter_options": [],
        "appointment_price_map": calculate_appointment_price_map(db, pending_appointments),
        "pending_count": len(pending_appointments),
        "completed_count": completed_count,
        "can_manage_appointments": is_manager(user),
        "show_create_card": False,
        "show_actions": True,
        "section_path": "/appointments",
        "create_modal_open": False,
        "is_calendar_day_view": True,
        "calendar_day_iso": selected_day.isoformat(),
        "calendar_selected_master": selected_master,
    }


def appointment_form_defaults(
    *,
    appointment: Appointment | None = None,
    day_value: str | None = None,
    selected_master: User | None = None,
) -> dict[str, object]:
    default_date = appointment.starts_at.date() if appointment else parse_day(day_value)
    default_time = appointment.starts_at.strftime("%H:%M") if appointment else "10:00"
    return {
        "appointment_id": appointment.id if appointment else None,
        "date": default_date.isoformat(),
        "time": default_time,
        "master_id": appointment.master_id if appointment else (selected_master.id if selected_master else None),
        "client_mode": "existing" if appointment and appointment.client_id else "guest",
        "client_id": appointment.client_id if appointment else None,
        "guest_label": appointment.guest_label if appointment and not appointment.client_id else "Гость",
        "service_ids": [service.id for service in appointment.services] if appointment else [],
        "comment": appointment.comment if appointment else "",
    }


def build_appointment_form_payload(
    db: Session,
    user: User,
    *,
    appointment: Appointment | None = None,
    day_value: str | None = None,
    selected_master_id: int | None = None,
    form_data: dict[str, object] | None = None,
    error_message: str | None = None,
) -> dict[str, object]:
    visible_masters = get_visible_masters(db, user)
    selected_master = resolve_master_filter(db, user, selected_master_id)
    settings_obj = db.scalar(select(Setting))
    clients = list(db.scalars(select(Client).order_by(Client.full_name.asc())))
    services = list(db.scalars(select(Service).order_by(Service.name.asc())))
    form_state = form_data or appointment_form_defaults(
        appointment=appointment,
        day_value=day_value,
        selected_master=selected_master,
    )

    active_master_id = form_state.get("master_id")
    active_service_ids = form_state.get("service_ids", [])
    price_summary = PriceSummary(total=0, items=[], missing_service_names=[])
    if active_master_id and active_service_ids:
        price_summary = calculate_master_prices(db, int(active_master_id), [int(service_id) for service_id in active_service_ids])

    return {
        "appointment": appointment,
        "form_state": form_state,
        "clients": clients,
        "services": services,
        "visible_masters": visible_masters,
        "selected_master": selected_master,
        "settings_obj": settings_obj,
        "price_summary": price_summary,
        "form_error": error_message,
    }


def build_appointment_form_context(
    db: Session,
    user: User,
    request: Request,
    *,
    appointment: Appointment | None = None,
    day_value: str | None = None,
    selected_master_id: int | None = None,
    form_data: dict[str, object] | None = None,
    error_message: str | None = None,
) -> object:
    return page(
        request,
        "appointment_form.html",
        user,
        build_appointment_form_payload(
            db,
            user,
            appointment=appointment,
            day_value=day_value,
            selected_master_id=selected_master_id,
            form_data=form_data,
            error_message=error_message,
        ),
        status_code=status.HTTP_400_BAD_REQUEST if error_message else status.HTTP_200_OK,
        db=db,
    )


def assert_appointment_permissions(user: User, master_id: int) -> str | None:
    if is_manager(user):
        return None
    if user.role == UserRole.MASTER and user.id == master_id:
        return None
    return "Недостаточно прав для работы с выбранным мастером."


def parse_start_datetime(day_value: str, time_value: str) -> datetime | None:
    try:
        return datetime.combine(
            datetime.strptime(day_value, "%Y-%m-%d").date(),
            datetime.strptime(time_value, "%H:%M").time(),
        )
    except ValueError:
        return None


def validate_appointment_payload(
    db: Session,
    *,
    user: User,
    appointment_id: int | None,
    day_value: str,
    time_value: str,
    master_id: int,
    client_mode: str,
    client_id: int | None,
    guest_label: str | None,
    service_ids: list[int],
) -> tuple[datetime | None, str | None]:
    starts_at = parse_start_datetime(day_value, time_value)
    if not starts_at:
        return None, "Укажите корректные дату и время записи."

    permission_error = assert_appointment_permissions(user, master_id)
    if permission_error:
        return None, permission_error

    master = db.scalar(select(User).where(User.id == master_id, User.role == UserRole.MASTER))
    if not master:
        return None, "Выберите мастера."

    settings_obj = db.scalar(select(Setting))
    if settings_obj and not enforce_working_hours(starts_at, settings_obj.salon_open_time, settings_obj.salon_close_time):
        return None, "Выбранное время выходит за рамки рабочих часов салона."

    duplicate_query = select(Appointment).where(
        Appointment.master_id == master_id,
        Appointment.starts_at == starts_at,
    )
    if appointment_id:
        duplicate_query = duplicate_query.where(Appointment.id != appointment_id)
    if db.scalar(duplicate_query):
        return None, "У этого мастера уже есть запись на выбранное время."

    if client_mode == "existing":
        if not client_id or not db.get(Client, client_id):
            return None, "Выберите клиента из базы."
    else:
        if not guest_label or not guest_label.strip():
            return None, "Для гостя укажите имя или пометку."

    if not service_ids:
        return None, "Выберите хотя бы одну услугу."

    pricing = calculate_master_prices(db, master_id, service_ids)
    if pricing.missing_service_names:
        return None, "Для мастера не заданы цены на: " + ", ".join(pricing.missing_service_names)

    return starts_at, None


LANDING_PHONE_MAX_LEN = 30
LANDING_NAME_MAX_LEN = 255
LANDING_COMMENT_MAX_LEN = 1000


def _public_master_query() -> Select:
    return (
        select(User)
        .where(User.role == UserRole.MASTER)
        .order_by(User.full_name.asc())
    )


def _public_services_query() -> Select:
    return select(Service).order_by(Service.name.asc())


def _time_label(value: time | None) -> str:
    if value is None:
        return "10:00"
    return value.strftime("%H:%M")


def _empty_booking_form() -> dict[str, object]:
    return {
        "full_name": "",
        "phone": "",
        "preferred_date": "",
        "preferred_time": "",
        "preferred_master_id": None,
        "comment": "",
        "service_ids": [],
    }


def render_landing(
    request: Request,
    db: Session,
    *,
    user: User | None,
    booking_form: dict[str, object] | None = None,
    booking_error: str | None = None,
    booking_success: bool = False,
    status_code: int = status.HTTP_200_OK,
) -> object:
    services = list(db.scalars(_public_services_query()))
    masters = list(db.scalars(_public_master_query()))
    settings_obj = db.scalar(select(Setting))
    open_label = _time_label(settings_obj.salon_open_time if settings_obj else None)
    close_label = _time_label(settings_obj.salon_close_time if settings_obj else None)
    payload = {
        "request": request,
        "current_user": user,
        "landing_services": services,
        "landing_masters": masters,
        "salon_open_label": open_label,
        "salon_close_label": close_label,
        "booking_min_date": date.today().isoformat(),
        "booking_form": booking_form or _empty_booking_form(),
        "booking_error": booking_error,
        "booking_success": booking_success,
    }
    return templates.TemplateResponse(
        "landing.html",
        payload,
        status_code=status_code,
    )


def validate_public_booking_payload(
    db: Session,
    *,
    full_name: str,
    phone: str,
    preferred_date_value: str,
    preferred_time_value: str,
    preferred_master_id: int | None,
    service_ids: list[int],
) -> tuple[date | None, time | None, list[Service], User | None, str | None]:
    clean_name = full_name.strip()
    if not clean_name:
        return None, None, [], None, "Укажите имя."
    if len(clean_name) > LANDING_NAME_MAX_LEN:
        return None, None, [], None, "Имя слишком длинное."

    clean_phone = phone.strip()
    if not clean_phone:
        return None, None, [], None, "Укажите номер телефона для связи."
    if len(clean_phone) > LANDING_PHONE_MAX_LEN:
        return None, None, [], None, "Номер телефона слишком длинный."

    try:
        preferred_date_obj = datetime.strptime(preferred_date_value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None, None, [], None, "Укажите корректную дату визита."

    try:
        preferred_time_obj = datetime.strptime(preferred_time_value.strip(), "%H:%M").time()
    except (ValueError, AttributeError):
        return None, None, [], None, "Укажите корректное время визита."

    starts_at = datetime.combine(preferred_date_obj, preferred_time_obj)
    if starts_at <= datetime.now():
        return None, None, [], None, "Выберите дату и время в будущем."

    settings_obj = db.scalar(select(Setting))
    if settings_obj and not enforce_working_hours(
        starts_at, settings_obj.salon_open_time, settings_obj.salon_close_time
    ):
        return None, None, [], None, "Выбранное время выходит за рамки рабочих часов салона."

    unique_service_ids = list(dict.fromkeys(service_ids))
    if not unique_service_ids:
        return None, None, [], None, "Выберите хотя бы одну услугу."

    services = list(
        db.scalars(
            select(Service)
            .where(Service.id.in_(unique_service_ids))
            .order_by(Service.name.asc())
        )
    )
    if len(services) != len(unique_service_ids):
        return None, None, [], None, "Одна из выбранных услуг не найдена."

    master: User | None = None
    if preferred_master_id:
        master = db.scalar(
            select(User).where(
                User.id == preferred_master_id,
                User.role == UserRole.MASTER,
            )
        )
        if not master:
            return None, None, [], None, "Выбранный мастер недоступен."

    return preferred_date_obj, preferred_time_obj, services, master, None


@router.get("/", include_in_schema=False)
def root(
    request: Request,
    submitted: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> object:
    user = get_current_user(request, db)
    return render_landing(
        request,
        db,
        user=user,
        booking_success=bool(submitted),
    )


@router.post("/booking-requests", include_in_schema=False)
def public_booking_request_create(
    request: Request,
    full_name: str = Form(...),
    phone: str = Form(...),
    preferred_date: str = Form(...),
    preferred_time: str = Form(...),
    preferred_master_id_raw: str | None = Form(default=None, alias="preferred_master_id"),
    service_ids: list[int] = Form(default=[]),
    comment: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> object:
    user = get_current_user(request, db)
    preferred_master_id = to_optional_int(preferred_master_id_raw)
    clean_comment = (comment or "").strip()
    if len(clean_comment) > LANDING_COMMENT_MAX_LEN:
        clean_comment = clean_comment[:LANDING_COMMENT_MAX_LEN]

    (
        preferred_date_obj,
        preferred_time_obj,
        services,
        master,
        error_message,
    ) = validate_public_booking_payload(
        db,
        full_name=full_name,
        phone=phone,
        preferred_date_value=preferred_date,
        preferred_time_value=preferred_time,
        preferred_master_id=preferred_master_id,
        service_ids=service_ids,
    )

    if error_message or not preferred_date_obj or not preferred_time_obj:
        form_state: dict[str, object] = {
            "full_name": full_name,
            "phone": phone,
            "preferred_date": preferred_date,
            "preferred_time": preferred_time,
            "preferred_master_id": preferred_master_id,
            "comment": clean_comment,
            "service_ids": list(dict.fromkeys(service_ids)),
        }
        return render_landing(
            request,
            db,
            user=user,
            booking_form=form_state,
            booking_error=error_message or "Не удалось сохранить заявку.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    booking_request = BookingRequest(
        full_name=full_name.strip(),
        phone=phone.strip(),
        preferred_master_id=master.id if master else None,
        preferred_date=preferred_date_obj,
        preferred_time=preferred_time_obj,
        comment=clean_comment or None,
        status=BookingRequestStatus.NEW,
    )
    booking_request.services = services
    db.add(booking_request)
    db.commit()
    return redirect_to("/", submitted=1)


def _booking_request_base_query() -> Select:
    return (
        select(BookingRequest)
        .options(
            selectinload(BookingRequest.services),
            joinedload(BookingRequest.preferred_master),
            joinedload(BookingRequest.appointment).joinedload(Appointment.master),
            joinedload(BookingRequest.appointment).joinedload(Appointment.client),
            joinedload(BookingRequest.resolved_by),
        )
    )


def _normalize_phone_for_match(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())


def _find_client_candidates(db: Session, phone: str, full_name: str) -> list[Client]:
    normalized_phone = _normalize_phone_for_match(phone)
    candidates: list[Client] = []
    seen_ids: set[int] = set()

    if normalized_phone:
        tail = normalized_phone[-7:] if len(normalized_phone) >= 7 else normalized_phone
        phone_matches = list(
            db.scalars(
                select(Client)
                .where(Client.phone.like(f"%{tail}%"))
                .order_by(Client.full_name.asc())
                .limit(10)
            )
        )
        for client in phone_matches:
            if client.id not in seen_ids:
                seen_ids.add(client.id)
                candidates.append(client)

    clean_name = (full_name or "").strip()
    if clean_name and len(candidates) < 10:
        name_matches = list(
            db.scalars(
                select(Client)
                .where(Client.full_name.ilike(f"%{clean_name}%"))
                .order_by(Client.full_name.asc())
                .limit(10)
            )
        )
        for client in name_matches:
            if client.id not in seen_ids:
                seen_ids.add(client.id)
                candidates.append(client)

    return candidates[:10]


def build_booking_requests_list_context(
    db: Session,
    *,
    section: str,
) -> dict[str, object]:
    normalized_section = section if section in {"new", "processed"} else "new"

    new_query = (
        _booking_request_base_query()
        .where(BookingRequest.status == BookingRequestStatus.NEW)
        .order_by(BookingRequest.created_at.desc())
    )
    processed_query = (
        _booking_request_base_query()
        .where(BookingRequest.status != BookingRequestStatus.NEW)
        .order_by(BookingRequest.resolved_at.desc().nullslast(), BookingRequest.created_at.desc())
        .limit(100)
    )

    new_requests = list(db.scalars(new_query).unique())
    processed_requests = list(db.scalars(processed_query).unique())

    new_count = len(new_requests)
    processed_count = db.scalar(
        select(func.count(BookingRequest.id)).where(
            BookingRequest.status != BookingRequestStatus.NEW
        )
    ) or 0

    requests_to_show = new_requests if normalized_section == "new" else processed_requests

    return {
        "page_section": normalized_section,
        "booking_requests": requests_to_show,
        "new_requests_count": new_count,
        "processed_requests_count": processed_count,
    }


def build_booking_request_detail_context(
    db: Session,
    booking_request: BookingRequest,
) -> dict[str, object]:
    visible_masters = list(db.scalars(_public_master_query()))
    all_services = list(db.scalars(_public_services_query()))
    selected_service_ids = [service.id for service in booking_request.services]
    client_candidates = _find_client_candidates(
        db,
        booking_request.phone,
        booking_request.full_name,
    )

    default_master_id = (
        booking_request.preferred_master_id
        if booking_request.preferred_master_id
        else (visible_masters[0].id if visible_masters else None)
    )

    default_client_mode = "existing" if client_candidates else "new"
    default_client_id = client_candidates[0].id if client_candidates else None

    return {
        "booking_request": booking_request,
        "visible_masters": visible_masters,
        "all_services": all_services,
        "selected_service_ids": selected_service_ids,
        "client_candidates": client_candidates,
        "default_master_id": default_master_id,
        "default_client_mode": default_client_mode,
        "default_client_id": default_client_id,
    }


@router.get("/booking-requests", include_in_schema=False)
def booking_requests_page(
    request: Request,
    section: str | None = Query(default="new"),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    context = build_booking_requests_list_context(db, section=section or "new")
    return page(request, "booking_requests.html", user, context, db=db)


@router.get("/booking-requests/{request_id}", include_in_schema=False)
def booking_request_detail_page(
    request_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    booking_request = db.scalar(
        _booking_request_base_query().where(BookingRequest.id == request_id)
    )
    if not booking_request:
        return redirect_to("/booking-requests", error="Заявка не найдена.")

    context = build_booking_request_detail_context(db, booking_request)
    return page(request, "booking_request_detail.html", user, context, db=db)


@router.post("/booking-requests/{request_id}/approve", include_in_schema=False)
def booking_request_approve(
    request_id: int,
    request: Request,
    day_value: str = Form(..., alias="date"),
    time_value: str = Form(..., alias="time"),
    master_id: int = Form(...),
    client_mode: str = Form(...),
    client_id_raw: str | None = Form(default=None, alias="client_id"),
    service_ids: list[int] = Form(default=[]),
    comment: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    booking_request = db.scalar(
        _booking_request_base_query().where(BookingRequest.id == request_id)
    )
    if not booking_request:
        return redirect_to("/booking-requests", error="Заявка не найдена.")

    if booking_request.status != BookingRequestStatus.NEW:
        return redirect_to(
            f"/booking-requests/{request_id}",
            error="Эта заявка уже обработана.",
        )

    client_id = to_optional_int(client_id_raw)
    normalized_mode = client_mode if client_mode in {"existing", "new"} else "new"

    guest_label = None
    if normalized_mode == "new":
        guest_label = booking_request.full_name.strip() or "Гость"

    starts_at, error_message = validate_appointment_payload(
        db,
        user=user,
        appointment_id=None,
        day_value=day_value,
        time_value=time_value,
        master_id=master_id,
        client_mode=normalized_mode,
        client_id=client_id,
        guest_label=guest_label,
        service_ids=service_ids,
    )

    if error_message or not starts_at:
        context = build_booking_request_detail_context(db, booking_request)
        context["form_error"] = error_message or "Не удалось подтвердить заявку."
        context["form_state"] = {
            "date": day_value,
            "time": time_value,
            "master_id": master_id,
            "client_mode": normalized_mode,
            "client_id": client_id,
            "service_ids": list(dict.fromkeys(service_ids)),
            "comment": comment or "",
        }
        return page(
            request,
            "booking_request_detail.html",
            user,
            context,
            status_code=status.HTTP_400_BAD_REQUEST,
            db=db,
        )

    resolved_client: Client | None = None
    if normalized_mode == "existing" and client_id:
        resolved_client = db.get(Client, client_id)

    if normalized_mode == "new":
        snapshot_phone = booking_request.phone.strip()
        existing_by_phone = None
        if snapshot_phone:
            existing_by_phone = db.scalar(
                select(Client).where(Client.phone == snapshot_phone)
            )
        if existing_by_phone:
            resolved_client = existing_by_phone
        else:
            resolved_client = Client(
                full_name=booking_request.full_name.strip() or "Гость",
                phone=snapshot_phone or "",
            )
            db.add(resolved_client)
            db.flush()

    services = list(
        db.scalars(
            select(Service)
            .where(Service.id.in_(service_ids))
            .order_by(Service.name.asc())
        )
    )

    appointment = Appointment(
        client_id=resolved_client.id if resolved_client else None,
        guest_label=None if resolved_client else (guest_label or "Гость"),
        master_id=master_id,
        starts_at=starts_at,
        status=AppointmentStatus.PENDING,
        comment=(comment or "").strip() or None,
    )
    appointment.services = services
    db.add(appointment)
    db.flush()

    booking_request.status = BookingRequestStatus.APPROVED
    booking_request.appointment_id = appointment.id
    booking_request.resolved_by_user_id = user.id
    booking_request.resolved_at = utc_now()
    db.commit()

    return redirect_to(
        "/booking-requests",
        section="processed",
        notice="Заявка подтверждена, запись создана.",
    )


@router.post("/booking-requests/{request_id}/decline", include_in_schema=False)
def booking_request_decline(
    request_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    booking_request = db.scalar(
        select(BookingRequest).where(BookingRequest.id == request_id)
    )
    if not booking_request:
        return redirect_to("/booking-requests", error="Заявка не найдена.")

    if booking_request.status != BookingRequestStatus.NEW:
        return redirect_to(
            f"/booking-requests/{request_id}",
            error="Эта заявка уже обработана.",
        )

    booking_request.status = BookingRequestStatus.DECLINED
    booking_request.resolved_by_user_id = user.id
    booking_request.resolved_at = utc_now()
    db.commit()

    return redirect_to(
        "/booking-requests",
        section="processed",
        notice="Заявка отклонена.",
    )


@router.get("/login", include_in_schema=False)
def login_page(request: Request, db: Session = Depends(get_db)) -> object:
    user = get_current_user(request, db)
    if user:
        return redirect_to("/appointments")
    return page(request, "login.html", None)


@router.post("/login", include_in_schema=False)
def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> object:
    user = db.scalar(select(User).where(User.username == username.strip()))
    if not user or not verify_password(password, user.password_hash):
        return page(
            request,
            "login.html",
            None,
            {"form_error": "Неверный логин или пароль."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    token = create_access_token(subject=user.username, extra_claims={"role": user.role.value})
    response = redirect_to("/appointments", notice="Вы вошли в систему.")
    settings = get_settings()
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.auth_cookie_secure,
        max_age=settings.access_token_expire_minutes * 60,
    )
    return response


@router.post("/logout", include_in_schema=False)
def logout() -> RedirectResponse:
    response = redirect_to("/login", notice="Сессия завершена.")
    response.delete_cookie(get_settings().auth_cookie_name)
    return response


@router.get("/dashboard", include_in_schema=False)
def dashboard(
    request: Request,
    master_id_raw: str | None = Query(default=None, alias="master_id"),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    return page(
        request,
        "dashboard.html",
        user,
        build_dashboard_context(db, user, parse_optional_int_query(master_id_raw)),
        db=db,
    )


@router.get("/reports", include_in_schema=False)
def reports_page(
    request: Request,
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    master_id_raw: str | None = Query(default=None, alias="master_id"),
    service_id_raw: str | None = Query(default=None, alias="service_id"),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_director_access(user)
    if restricted:
        return restricted

    return page(
        request,
        "reports.html",
        user,
        build_reports_context(
            db,
            user,
            date_from_value=date_from,
            date_to_value=date_to,
            selected_master_id=parse_optional_int_query(master_id_raw),
            selected_service_id=parse_optional_int_query(service_id_raw),
        ),
        db=db,
    )


@router.get("/calendar", include_in_schema=False)
def calendar_page(
    request: Request,
    month: str | None = Query(default=None),
    master_id_raw: str | None = Query(default=None, alias="master_id"),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user
    return page(
        request,
        "calendar.html",
        user,
        build_calendar_context(db, user, month, parse_optional_int_query(master_id_raw)),
        db=db,
    )


@router.get("/calendar/day", include_in_schema=False)
def calendar_day_page(
    request: Request,
    day: str | None = Query(default=None),
    master_id_raw: str | None = Query(default=None, alias="master_id"),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    master_id = parse_optional_int_query(master_id_raw)
    selected_master = resolve_master_filter(db, user, master_id)
    selected_day = parse_day(day)
    return page(
        request,
        "appointments.html",
        user,
        build_calendar_day_appointments_context(db, user, selected_day, selected_master),
        db=db,
    )


@router.get("/appointments", include_in_schema=False)
def appointments_page(
    request: Request,
    section: str | None = Query(default="pending"),
    period: str | None = Query(default="today"),
    master_id_raw: str | None = Query(default=None, alias="master_id"),
    service_id_raw: str | None = Query(default=None, alias="service_id"),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    return page(
        request,
        "appointments.html",
        user,
        build_appointments_board_context(
            db,
            user,
            section_value=section,
            period_value=period,
            selected_master_id=parse_optional_int_query(master_id_raw),
            selected_service_id=parse_optional_int_query(service_id_raw),
        ),
        db=db,
    )


@router.get("/appointments/pending", include_in_schema=False)
def appointments_pending_redirect() -> RedirectResponse:
    return redirect_to("/appointments", section="pending")


@router.get("/appointments/completed", include_in_schema=False)
def appointments_completed_redirect() -> RedirectResponse:
    return redirect_to("/appointments", section="completed")


@router.get("/appointments/new", include_in_schema=False)
def appointment_new_page(
    request: Request,
    day: str | None = Query(default=None),
    master_id_raw: str | None = Query(default=None, alias="master_id"),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_appointment_create_access(user)
    if restricted:
        return restricted

    return build_appointment_form_context(
        db,
        user,
        request,
        day_value=day,
        selected_master_id=parse_optional_int_query(master_id_raw),
    )


@router.post("/appointments", include_in_schema=False)
def appointment_create(
    request: Request,
    day_value: str = Form(..., alias="date"),
    time_value: str = Form(..., alias="time"),
    master_id: int = Form(...),
    client_mode: str = Form(...),
    client_id_raw: str | None = Form(default=None, alias="client_id"),
    guest_label: str | None = Form(default=None),
    service_ids: list[int] = Form(...),
    comment: str | None = Form(default=None),
    source: str | None = Form(default=None),
    return_section: str | None = Form(default="pending"),
    return_period: str | None = Form(default="today"),
    return_master_id_raw: str | None = Form(default=None, alias="return_master_id"),
    return_service_id_raw: str | None = Form(default=None, alias="return_service_id"),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_appointment_create_access(user)
    if restricted:
        return restricted

    client_id = to_optional_int(client_id_raw)
    starts_at, error_message = validate_appointment_payload(
        db,
        user=user,
        appointment_id=None,
        day_value=day_value,
        time_value=time_value,
        master_id=master_id,
        client_mode=client_mode,
        client_id=client_id,
        guest_label=guest_label,
        service_ids=service_ids,
    )
    form_state = {
        "date": day_value,
        "time": time_value,
        "master_id": master_id,
        "client_mode": client_mode,
        "client_id": client_id,
        "guest_label": None if client_mode == "existing" else (guest_label or "Гость").strip(),
        "service_ids": service_ids,
        "comment": comment or "",
    }
    if error_message or not starts_at:
        if source == "appointments-board":
            return page(
                request,
                "appointments.html",
                user,
                build_appointments_board_context(
                    db,
                    user,
                    section_value=return_section,
                    period_value=return_period,
                    selected_master_id=parse_optional_int_query(return_master_id_raw),
                    selected_service_id=parse_optional_int_query(return_service_id_raw),
                    create_form_data=form_state,
                    create_form_error=error_message,
                    create_modal_open=True,
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
                db=db,
            )
        return build_appointment_form_context(
            db,
            user,
            request,
            selected_master_id=master_id,
            form_data=form_state,
            error_message=error_message,
        )

    appointment = Appointment(
        client_id=client_id if client_mode == "existing" else None,
        guest_label=None if client_mode == "existing" else (guest_label or "Гость").strip(),
        master_id=master_id,
        starts_at=starts_at,
        status=AppointmentStatus.PENDING,
        comment=(comment or "").strip() or None,
    )
    appointment.services = list(db.scalars(select(Service).where(Service.id.in_(service_ids)).order_by(Service.name.asc())))
    db.add(appointment)
    db.commit()
    if source == "appointments-board":
        return redirect_to(
            "/appointments",
            section="pending",
            period=parse_appointment_filter(return_period),
            master_id=parse_optional_int_query(return_master_id_raw),
            service_id=parse_optional_int_query(return_service_id_raw),
            notice="Запись создана.",
        )
    return redirect_to("/calendar/day", day=starts_at.date().isoformat(), master_id=master_id, notice="Запись создана.")


@router.get("/appointments/{appointment_id}/edit", include_in_schema=False)
def appointment_edit_page(
    appointment_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    appointment = db.scalar(
        select(Appointment)
        .options(selectinload(Appointment.services), joinedload(Appointment.client), joinedload(Appointment.master))
        .where(Appointment.id == appointment_id)
    )
    if not appointment:
        return redirect_to("/calendar", error="Запись не найдена.")
    if appointment.status != AppointmentStatus.PENDING:
        return redirect_to(
            "/calendar/day",
            day=appointment.starts_at.date().isoformat(),
            master_id=appointment.master_id,
            error="Редактировать можно только ожидающие записи.",
        )

    return build_appointment_form_context(
        db,
        user,
        request,
        appointment=appointment,
        selected_master_id=appointment.master_id,
    )


@router.post("/appointments/{appointment_id}", include_in_schema=False)
def appointment_update(
    appointment_id: int,
    request: Request,
    day_value: str = Form(..., alias="date"),
    time_value: str = Form(..., alias="time"),
    master_id: int = Form(...),
    client_mode: str = Form(...),
    client_id_raw: str | None = Form(default=None, alias="client_id"),
    guest_label: str | None = Form(default=None),
    service_ids: list[int] = Form(...),
    comment: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    client_id = to_optional_int(client_id_raw)
    appointment = db.scalar(select(Appointment).options(selectinload(Appointment.services)).where(Appointment.id == appointment_id))
    if not appointment:
        return redirect_to("/calendar", error="Запись не найдена.")
    if appointment.status != AppointmentStatus.PENDING:
        return redirect_to(
            "/calendar/day",
            day=appointment.starts_at.date().isoformat(),
            master_id=appointment.master_id,
            error="Редактировать можно только ожидающие записи.",
        )

    starts_at, error_message = validate_appointment_payload(
        db,
        user=user,
        appointment_id=appointment_id,
        day_value=day_value,
        time_value=time_value,
        master_id=master_id,
        client_mode=client_mode,
        client_id=client_id,
        guest_label=guest_label,
        service_ids=service_ids,
    )
    form_state = {
        "appointment_id": appointment_id,
        "date": day_value,
        "time": time_value,
        "master_id": master_id,
        "client_mode": client_mode,
        "client_id": client_id,
        "guest_label": None if client_mode == "existing" else (guest_label or "Гость").strip(),
        "service_ids": service_ids,
        "comment": comment or "",
    }
    if error_message or not starts_at:
        return build_appointment_form_context(
            db,
            user,
            request,
            appointment=appointment,
            selected_master_id=master_id,
            form_data=form_state,
            error_message=error_message,
        )

    appointment.client_id = client_id if client_mode == "existing" else None
    appointment.guest_label = None if client_mode == "existing" else (guest_label or "Гость").strip()
    appointment.master_id = master_id
    appointment.starts_at = starts_at
    appointment.comment = (comment or "").strip() or None
    appointment.services = list(db.scalars(select(Service).where(Service.id.in_(service_ids)).order_by(Service.name.asc())))
    db.commit()
    return redirect_to("/calendar/day", day=starts_at.date().isoformat(), master_id=master_id, notice="Запись обновлена.")


@router.post("/appointments/{appointment_id}/status", include_in_schema=False)
def appointment_status_update(
    appointment_id: int,
    request: Request,
    appointment_status: str = Form(..., alias="status"),
    period: str | None = Form(default="today"),
    section: str | None = Form(default="pending"),
    day: str | None = Form(default=None),
    master_id_raw: str | None = Form(default=None, alias="master_id"),
    service_id_raw: str | None = Form(default=None, alias="service_id"),
    completion_comment: str | None = Form(default=None),
    receipt_photos: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    safe_section = section if section in {"pending", "completed"} else "pending"
    safe_day = parse_day(day).isoformat() if day else None
    safe_master_id = parse_optional_int_query(master_id_raw)
    safe_service_id = parse_optional_int_query(service_id_raw)

    def status_redirect(*, notice: str | None = None, error: str | None = None) -> RedirectResponse:
        if safe_day:
            return redirect_to(
                "/calendar/day",
                day=safe_day,
                master_id=safe_master_id,
                notice=notice,
                error=error,
            )
        return redirect_to(
            "/appointments",
            section=safe_section,
            period=parse_appointment_filter(period),
            master_id=safe_master_id,
            service_id=safe_service_id,
            notice=notice,
            error=error,
        )

    appointment = db.scalar(
        select(Appointment)
        .options(
            joinedload(Appointment.master),
            selectinload(Appointment.receipt_photos),
        )
        .where(Appointment.id == appointment_id)
    )
    if not appointment:
        return status_redirect(error="Запись не найдена.")

    permission_error = assert_appointment_permissions(user, appointment.master_id)
    if permission_error:
        return status_redirect(error=permission_error)

    target_status = appointment_status.strip().lower()
    if target_status == AppointmentStatus.COMPLETED.value:
        clean_completion_comment = (completion_comment or "").strip()
        uploaded_photo_paths: list[str] = []
        appointment.status = AppointmentStatus.COMPLETED
        try:
            for upload in receipt_photos:
                file_path = save_receipt_photo(upload)
                if file_path:
                    uploaded_photo_paths.append(file_path)
        except ValueError as exc:
            db.rollback()
            return status_redirect(error=str(exc))
        if not uploaded_photo_paths and not clean_completion_comment:
            return status_redirect(error="Добавьте фото или комментарий перед завершением записи.")
        for file_path in uploaded_photo_paths:
            appointment.receipt_photos.append(AppointmentReceiptPhoto(file_path=file_path))
        appointment.completion_comment = clean_completion_comment or None
        appointment.completed_at = utc_now()
        notice = "Запись отмечена как выполненная."
    elif target_status == AppointmentStatus.CANCELLED.value:
        appointment.status = AppointmentStatus.CANCELLED
        appointment.completion_comment = None
        appointment.completed_at = None
        notice = "Запись отменена."
    else:
        return status_redirect(error="Некорректное действие для записи.")

    if appointment.master.availability_status == MasterAvailability.BUSY:
        appointment.master.availability_status = MasterAvailability.FREE
        appointment.master.status_changed_at = utc_now()

    db.commit()
    return status_redirect(notice=notice)


@router.post("/appointments/{appointment_id}/delete", include_in_schema=False)
def appointment_delete(
    appointment_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        return redirect_to("/calendar", error="Запись не найдена.")

    day_value = appointment.starts_at.date().isoformat()
    master_id = appointment.master_id
    db.delete(appointment)
    db.commit()
    return redirect_to("/calendar/day", day=day_value, master_id=master_id, notice="Запись удалена.")


@router.get("/clients", include_in_schema=False)
def clients_page(
    request: Request,
    q: str | None = Query(default=None),
    edit: int | None = Query(default=None),
    client_edit: int | None = Query(default=None),
    client_create: bool = Query(default=False),
    clients_edit: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    query = select(Client).order_by(Client.full_name.asc())
    if user.role == UserRole.MASTER:
        query = (
            query.join(Appointment, Appointment.client_id == Client.id)
            .where(Appointment.master_id == user.id)
            .distinct()
        )

    editing_client_id = client_edit or edit if is_manager(user) else None
    editing_client = db.get(Client, editing_client_id) if editing_client_id and is_manager(user) else None
    clients = list(db.scalars(query))
    if q and q.strip():
        clients = [client for client in clients if client_matches_search(client, q)]
    client_ids = [client.id for client in clients]
    client_appointment_counts: dict[int, int] = {}
    if client_ids:
        counts_query = (
            select(Appointment.client_id, func.count(Appointment.id))
            .where(Appointment.client_id.in_(client_ids))
            .group_by(Appointment.client_id)
        )
        if user.role == UserRole.MASTER:
            counts_query = counts_query.where(Appointment.master_id == user.id)
        counts = db.execute(counts_query).all()
        client_appointment_counts = {
            client_id: count
            for client_id, count in counts
            if client_id is not None
        }
    edit_clients = is_manager(user) and (clients_edit or client_create or editing_client is not None)
    return page(
        request,
        "clients.html",
        user,
        {
            "clients": clients,
            "client_appointment_counts": client_appointment_counts,
            "editing_client": editing_client,
            "edit_clients": edit_clients,
            "can_manage_clients": is_manager(user),
            "search_query": q or "",
            "show_client_modal": is_manager(user) and (client_create or editing_client is not None),
        },
        db=db,
    )


@router.get("/clients/{client_id}", include_in_schema=False)
def client_detail_page(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    client = db.get(Client, client_id)
    if not client:
        return redirect_to("/appointments", error="Клиент не найден.")

    if not can_view_client(user, client_id, db):
        return redirect_to("/appointments", error="Недостаточно прав для просмотра профиля клиента.")

    appointments_query = (
        appointment_base_query()
        .where(Appointment.client_id == client_id)
        .order_by(None)
        .order_by(Appointment.starts_at.desc())
    )
    appointments_query = apply_master_scope(appointments_query, user, None)

    return page(
        request,
        "client_detail.html",
        user,
        {
            "client": client,
            "appointments": list(db.scalars(appointments_query).unique()),
        },
        db=db,
    )


@router.post("/clients/save", include_in_schema=False)
def clients_save(
    request: Request,
    client_id_raw: str | None = Form(default=None, alias="client_id"),
    full_name: str = Form(...),
    phone: str = Form(...),
    instagram: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    avatar: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    client_id = to_optional_int(client_id_raw)
    existing_phone_owner = db.scalar(select(Client).where(Client.phone == phone.strip()))
    if existing_phone_owner and existing_phone_owner.id != client_id:
        return redirect_to(
            "/clients",
            error="Клиент с таким номером уже существует.",
            clients_edit=1,
            client_edit=client_id,
            client_create=None if client_id else True,
        )

    client = db.get(Client, client_id) if client_id else Client()
    if not client:
        return redirect_to("/clients", error="Клиент не найден.")

    client.full_name = full_name.strip()
    client.phone = phone.strip()
    client.instagram = (instagram or "").strip() or None
    client.notes = (notes or "").strip() or None
    if avatar and avatar.filename:
        try:
            client.avatar_path = save_avatar(avatar)
        except ValueError as exc:
            return redirect_to(
                "/clients",
                error=str(exc),
                clients_edit=1,
                client_edit=client_id,
                client_create=None if client_id else True,
            )
    db.add(client)
    db.commit()
    return redirect_to("/clients", notice="Карточка клиента сохранена.", clients_edit=1)


@router.post("/clients/{client_id}/delete", include_in_schema=False)
def clients_delete(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    if db.scalar(select(func.count(Appointment.id)).where(Appointment.client_id == client_id)):
        return redirect_to(
            "/clients",
            error="Нельзя удалить клиента, у которого есть записи.",
            clients_edit=1 if request.query_params.get("clients_edit") == "1" else None,
        )

    client = db.get(Client, client_id)
    if client:
        db.delete(client)
        db.commit()
    return redirect_to(
        "/clients",
        notice="Клиент удален.",
        clients_edit=1 if request.query_params.get("clients_edit") == "1" else None,
    )


@router.get("/services", include_in_schema=False)
def services_page(
    request: Request,
    edit: int | None = Query(default=None),
    master_id_raw: str | None = Query(default=None, alias="master_id"),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    parse_optional_int_query(master_id_raw)
    return redirect_to("/settings", settings_panel="services", service_edit=edit, services_edit=1)


@router.post("/services/save", include_in_schema=False)
def service_save(
    request: Request,
    service_id_raw: str | None = Form(default=None, alias="service_id"),
    name: str = Form(...),
    icon: str = Form(default=DEFAULT_SERVICE_ICON),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    service_id = to_optional_int(service_id_raw)
    clean_name = name.strip()
    selected_icon = icon.strip() or DEFAULT_SERVICE_ICON
    if selected_icon not in SERVICE_ICON_CHOICES:
        selected_icon = DEFAULT_SERVICE_ICON
    duplicate = db.scalar(select(Service).where(Service.name == clean_name))
    if duplicate and duplicate.id != service_id:
        return redirect_to("/settings", error="Услуга с таким названием уже существует.", settings_panel="services", service_edit=service_id, services_edit=1)

    service = db.get(Service, service_id) if service_id else Service()
    if not service:
        return redirect_to("/settings", error="Услуга не найдена.", settings_panel="services", services_edit=1)

    service.name = clean_name
    service.icon = selected_icon
    db.add(service)
    db.commit()
    return redirect_to("/settings", notice="Услуга сохранена.", settings_panel="services", services_edit=1)


@router.post("/services/{service_id}/delete", include_in_schema=False)
def service_delete(
    service_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    if db.scalar(select(func.count(MasterServicePrice.id)).where(MasterServicePrice.service_id == service_id)):
        return redirect_to("/settings", error="Сначала уберите услугу у мастеров в разделе команды.", settings_panel="services", services_edit=1)
    if db.scalar(select(func.count(Appointment.id)).join(Appointment.services).where(Service.id == service_id)):
        return redirect_to("/settings", error="Нельзя удалить услугу, которая уже использовалась в записях.", settings_panel="services", services_edit=1)

    service = db.get(Service, service_id)
    if service:
        db.delete(service)
        db.commit()
    return redirect_to("/settings", notice="Услуга удалена.", settings_panel="services", services_edit=1)


@router.post("/services/prices", include_in_schema=False)
async def master_prices_save(
    request: Request,
    master_id: int = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    master = db.scalar(select(User).where(User.id == master_id, User.role == UserRole.MASTER))
    if not master:
        return redirect_to("/team", error="Мастер не найден.")

    services = list(db.scalars(select(Service).order_by(Service.id.asc())))
    existing_prices = {
        price.service_id: price
        for price in db.scalars(select(MasterServicePrice).where(MasterServicePrice.master_id == master_id))
    }
    form = await request.form()

    for service in services:
        clean = str(form.get(f"price_{service.id}", "")).strip()
        existing = existing_prices.get(service.id)
        if not clean:
            if existing:
                db.delete(existing)
            continue
        if not clean.isdigit():
            return redirect_to(
                "/team",
                error=f"Цена для услуги «{service.name}» должна быть числом.",
                pricing_master=master_id,
            )
        if existing:
            existing.price = int(clean)
        else:
            db.add(MasterServicePrice(master_id=master_id, service_id=service.id, price=int(clean)))

    db.commit()
    return redirect_to("/team", notice=f"Услуги и цены мастера «{master.full_name}» обновлены.")


@router.get("/team", include_in_schema=False)
def team_page(
    request: Request,
    edit: int | None = Query(default=None),
    member_edit: int | None = Query(default=None),
    member_create: bool = Query(default=False),
    team_edit: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_director_access(user)
    if restricted:
        return restricted

    editing_user_id = member_edit or edit
    editing_user = db.get(User, editing_user_id) if editing_user_id else None
    members = list(db.scalars(select(User).order_by(User.role.asc(), User.full_name.asc())))
    services = list(db.scalars(select(Service).order_by(Service.name.asc())))
    master_ids = [member.id for member in members if member.role == UserRole.MASTER]
    master_price_map: dict[int, dict[int, int]] = {master_id: {} for master_id in master_ids}
    if master_ids:
        prices = list(
            db.scalars(
                select(MasterServicePrice).where(MasterServicePrice.master_id.in_(master_ids))
            )
        )
        for price in prices:
            master_price_map.setdefault(price.master_id, {})[price.service_id] = price.price
    master_service_items_map: dict[int, list[dict[str, object]]] = {master_id: [] for master_id in master_ids}
    for service in services:
        for master_id, service_price_map in master_price_map.items():
            price = service_price_map.get(service.id)
            if price is None:
                continue
            master_service_items_map.setdefault(master_id, []).append(
                {
                    "id": service.id,
                    "name": service.name,
                    "icon": service.icon or DEFAULT_SERVICE_ICON,
                    "price": price,
                }
            )
    edit_team = team_edit or member_create or editing_user is not None
    return page(
        request,
        "team.html",
        user,
        {
            "members": members,
            "editing_user": editing_user,
            "edit_team": edit_team,
            "role_choices": [UserRole.DIRECTOR, UserRole.ADMIN, UserRole.MASTER],
            "services": services,
            "master_price_map": master_price_map,
            "master_service_items_map": master_service_items_map,
            "show_member_modal": member_create or editing_user is not None,
        },
        db=db,
    )


@router.post("/team/save", include_in_schema=False)
async def team_save(
    request: Request,
    user_id_raw: str | None = Form(default=None, alias="user_id"),
    username: str = Form(...),
    password: str | None = Form(default=None),
    full_name: str = Form(...),
    role: UserRole = Form(...),
    avatar: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    viewer = ensure_authenticated(request, db)
    if isinstance(viewer, RedirectResponse):
        return viewer

    restricted = ensure_director_access(viewer)
    if restricted:
        return restricted

    user_id = to_optional_int(user_id_raw)
    duplicate_username = db.scalar(select(User).where(User.username == username.strip()))
    if duplicate_username and duplicate_username.id != user_id:
        return redirect_to(
            "/team",
            error="Пользователь с таким логином уже существует.",
            team_edit=1,
            member_edit=user_id,
            member_create=None if user_id else True,
        )

    member = db.get(User, user_id) if user_id else User()
    if not member:
        return redirect_to("/team", error="Пользователь не найден.")

    member.username = username.strip()
    member.full_name = full_name.strip()
    member.role = role
    if role == UserRole.MASTER and not (member.reminder_offsets or "").strip():
        member.reminder_offsets = "60"
    if role  != UserRole.MASTER:
        member.reminder_offsets = ""
    if not member.password_hash or (password and password.strip()):
        member.password_hash = get_password_hash((password or "").strip() or "changeme123")
    if avatar and avatar.filename:
        try:
            member.avatar_path = save_avatar(avatar)
        except ValueError as exc:
            return redirect_to(
                "/team",
                error=str(exc),
                team_edit=1,
                member_edit=user_id,
                member_create=None if user_id else True,
            )

    db.add(member)
    db.commit()
    return redirect_to("/team", notice="Пользователь сохранен.", team_edit=1)


@router.post("/team/{member_id}/delete", include_in_schema=False)
def team_delete(
    member_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    viewer = ensure_authenticated(request, db)
    if isinstance(viewer, RedirectResponse):
        return viewer

    restricted = ensure_director_access(viewer)
    if restricted:
        return restricted

    if member_id == viewer.id:
        return redirect_to("/team", error="Нельзя удалить текущего пользователя.", team_edit=1)

    member = db.get(User, member_id)
    if not member:
        return redirect_to("/team", error="Пользователь не найден.", team_edit=1)

    if member.role == UserRole.DIRECTOR:
        director_count = db.scalar(
            select(func.count(User.id)).where(User.role == UserRole.DIRECTOR)
        ) or 0
        if director_count <= 1:
            return redirect_to(
                "/team",
                error="В системе должен остаться хотя бы один директор.",
                team_edit=1,
            )

    appointments_count = db.scalar(
        select(func.count(Appointment.id)).where(Appointment.master_id == member_id)
    ) or 0
    if appointments_count:
        return redirect_to(
            "/team",
            error="Нельзя удалить пользователя, у которого есть записи.",
            team_edit=1,
        )

    db.delete(member)
    db.commit()
    return redirect_to("/team", notice="Пользователь удален.", team_edit=1)


@router.post("/team/{member_id}/status", include_in_schema=False)
def toggle_master_status(
    member_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    viewer = ensure_authenticated(request, db)
    if isinstance(viewer, RedirectResponse):
        return viewer

    if viewer.id  != member_id and not is_manager(viewer):
        return redirect_to("/team", error="Можно менять только собственный статус.")

    member = db.get(User, member_id)
    if not member or member.role  != UserRole.MASTER:
        return redirect_to("/team", error="Мастер не найден.")

    if member.availability_status == MasterAvailability.FREE:
        member.availability_status = MasterAvailability.BUSY
    else:
        member.availability_status = MasterAvailability.FREE
    member.status_changed_at = utc_now()
    db.commit()
    return redirect_to("/team", notice="Статус обновлен.")


@router.get("/settings", include_in_schema=False)
def settings_page(
    request: Request,
    settings_panel: str | None = Query(default=None),
    services_edit: bool = Query(default=False),
    service_edit: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> object:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    settings_obj = db.scalar(select(Setting))
    if not settings_obj:
        settings_obj = Setting()
        db.add(settings_obj)
        db.commit()
        db.refresh(settings_obj)

    services = list(db.scalars(select(Service).order_by(Service.name.asc())))
    editing_service = db.get(Service, service_edit) if service_edit else None
    edit_services = is_manager(user) and (services_edit or editing_service is not None)

    master_reminder_offsets: list[int] = []
    if user.role == UserRole.MASTER:
        master_reminder_offsets = parse_reminder_offsets(user.reminder_offsets)

    show_hours_modal = settings_panel == "hours"
    show_services_modal = settings_panel == "services" or edit_services or editing_service is not None

    return page(
        request,
        "settings.html",
        user,
        {
            "settings_obj": settings_obj,
            "services": services,
            "editing_service": editing_service,
            "edit_services": edit_services,
            "service_icon_choices": SERVICE_ICON_CHOICES,
            "master_reminder_offsets": master_reminder_offsets,
            "can_manage_hours": is_manager(user),
            "can_manage_services": is_manager(user),
            "show_hours_modal": show_hours_modal,
            "show_services_modal": show_services_modal,
        },
        db=db,
    )


@router.post("/settings", include_in_schema=False)
def settings_save(
    request: Request,
    salon_open_time: str = Form(...),
    salon_close_time: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    restricted = ensure_manager_access(user)
    if restricted:
        return restricted

    try:
        open_time = datetime.strptime(salon_open_time.strip(), "%H:%M").time()
        close_time = datetime.strptime(salon_close_time.strip(), "%H:%M").time()
    except ValueError:
        return redirect_to("/settings", error="Укажите корректное время.", settings_panel="hours")

    if close_time <= open_time:
        return redirect_to(
            "/settings",
            error="Время закрытия должно быть позже времени открытия.",
            settings_panel="hours",
        )

    settings_obj = db.scalar(select(Setting))
    if not settings_obj:
        settings_obj = Setting()
        db.add(settings_obj)
    settings_obj.salon_open_time = open_time
    settings_obj.salon_close_time = close_time
    db.commit()
    return redirect_to("/settings", notice="Настройки салона обновлены.")


@router.post("/settings/master-reminders", include_in_schema=False)
async def settings_master_reminders_save(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if user.role  != UserRole.MASTER:
        return redirect_to("/settings", error="Настройки напоминаний доступны только мастеру.")

    form = await request.form()
    raw_values = [str(value) for value in form.getlist("reminder_offsets")]
    try:
        offsets = normalize_reminder_offsets(raw_values)
    except ValueError as exc:
        return redirect_to("/settings", error=str(exc))

    user.reminder_offsets = ",".join(str(item) for item in offsets)
    db.commit()
    return redirect_to("/settings", notice="Напоминания сохранены. Отправку подключим в мобильном приложении.")


@router.get("/api/master-prices")
def master_prices_api(
    request: Request,
    master_id: int,
    service_ids: list[int] = Query(default=[]),
    db: Session = Depends(get_db),
) -> JSONResponse:
    user = ensure_authenticated(request, db)
    if isinstance(user, RedirectResponse):
        return JSONResponse({"detail": "Unauthorized"}, status_code=status.HTTP_401_UNAUTHORIZED)
    summary = calculate_master_prices(db, master_id, service_ids)
    return JSONResponse(
        {
            "total": summary.total,
            "items": summary.items,
            "missing_services": summary.missing_service_names,
        }
    )


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(
        BASE_DIR / "app" / "static" / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@router.get("/service-worker.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(BASE_DIR / "app" / "static" / "js" / "service-worker.js", media_type="application/javascript")


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}
