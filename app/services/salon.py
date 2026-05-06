from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta

from sqlalchemy import Select
from sqlalchemy import exists
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Appointment
from app.models.entities import AppointmentStatus
from app.models.entities import MasterAvailability
from app.models.entities import MasterServicePrice
from app.models.entities import Service
from app.models.entities import User
from app.models.entities import UserRole


ROLE_LABELS = {
    UserRole.DIRECTOR.value: "Директор",
    UserRole.ADMIN.value: "Администратор",
    UserRole.MASTER.value: "Мастер",
}

STATUS_LABELS = {
    MasterAvailability.FREE.value: "Свободен",
    MasterAvailability.BUSY.value: "Занят",
}

APPOINTMENT_STATUS_LABELS = {
    AppointmentStatus.PENDING.value: "Ожидает",
    AppointmentStatus.COMPLETED.value: "Выполнено",
    AppointmentStatus.CANCELLED.value: "Отменено",
    "in_progress": "В процессе",
}

APPOINTMENT_FILTER_LABELS = {
    "today": "Сегодня",
    "week": "Неделя",
    "month": "Месяц",
    "all": "Все время",
}

MONTH_LABELS = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}


@dataclass(slots=True)
class PriceSummary:
    total: int
    items: list[dict[str, int | str]]
    missing_service_names: list[str]


def role_label(role: UserRole | str) -> str:
    key = role.value if isinstance(role, UserRole) else role
    return ROLE_LABELS.get(key, str(key))


def status_label(status: MasterAvailability | str) -> str:
    key = status.value if isinstance(status, MasterAvailability) else status
    return STATUS_LABELS.get(key, str(key))


def appointment_status_label(status: AppointmentStatus | str) -> str:
    key = status.value if isinstance(status, AppointmentStatus) else status
    return APPOINTMENT_STATUS_LABELS.get(key, str(key))


def appointment_visual_status(appointment: Appointment) -> str:
    if appointment.status == AppointmentStatus.PENDING and appointment.starts_at <= datetime.now():
        return "in_progress"
    return appointment.status.value


def appointment_visual_status_label(appointment: Appointment) -> str:
    return appointment_status_label(appointment_visual_status(appointment))


def appointment_filter_label(value: str) -> str:
    return APPOINTMENT_FILTER_LABELS.get(value, APPOINTMENT_FILTER_LABELS["today"])


def appointment_filter_options() -> list[dict[str, str]]:
    return [{"value": value, "label": label} for value, label in APPOINTMENT_FILTER_LABELS.items()]


def parse_appointment_filter(value: str | None) -> str:
    if value in APPOINTMENT_FILTER_LABELS:
        return value
    return "today"


def month_label(value: date) -> str:
    return f"{MONTH_LABELS[value.month]} {value.year}"


def initials(value: str | None) -> str:
    if not value:
        return "?"
    parts = [part for part in value.replace("-", " ").split() if part]
    if not parts:
        return value[:1].upper()
    return "".join(part[0].upper() for part in parts[:2])


def is_manager(user: User) -> bool:
    return user.role in {UserRole.DIRECTOR, UserRole.ADMIN}


def is_director(user: User) -> bool:
    return user.role == UserRole.DIRECTOR


def today_range() -> tuple[datetime, datetime]:
    today = date.today()
    return datetime.combine(today, time.min), datetime.combine(today, time.max)


def appointment_period_bounds(value: str) -> tuple[datetime | None, datetime | None]:
    period = parse_appointment_filter(value)
    today = date.today()
    day_start = datetime.combine(today, time.min)

    if period == "today":
        return day_start, day_start + timedelta(days=1)
    if period == "week":
        return day_start, day_start + timedelta(days=7)
    if period == "month":
        month_start = today.replace(day=1)
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return datetime.combine(month_start, time.min), datetime.combine(next_month, time.min)
    return None, None


def parse_month(value: str | None) -> date:
    if value:
        try:
            parsed = datetime.strptime(value, "%Y-%m").date()
            return parsed.replace(day=1)
        except ValueError:
            pass
    today = date.today()
    return today.replace(day=1)


def parse_day(value: str | None) -> date:
    if value:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()


def month_bounds(month_start: date) -> tuple[datetime, datetime]:
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return datetime.combine(month_start, time.min), datetime.combine(next_month, time.min)


def month_weeks(month_start: date, appointments_by_day: dict[date, list[Appointment]]) -> list[list[dict[str, object]]]:
    month_calendar = calendar.Calendar(firstweekday=0)
    result: list[list[dict[str, object]]] = []

    for week in month_calendar.monthdatescalendar(month_start.year, month_start.month):
        cells: list[dict[str, object]] = []
        for current_day in week:
            day_appointments = appointments_by_day.get(current_day, [])
            masters = []
            seen_master_ids: set[int] = set()
            for appointment in day_appointments:
                if appointment.master_id not in seen_master_ids:
                    masters.append(appointment.master)
                    seen_master_ids.add(appointment.master_id)
            cells.append(
                {
                    "date": current_day,
                    "in_month": current_day.month == month_start.month,
                    "appointments": day_appointments,
                    "masters": masters[:4],
                    "count": len(day_appointments),
                }
            )
        result.append(cells)
    return result


def get_visible_masters(db: Session, user: User) -> list[User]:
    if user.role == UserRole.MASTER:
        return [user]
    return list(db.scalars(select(User).where(User.role == UserRole.MASTER).order_by(User.full_name.asc())))


def resolve_master_filter(db: Session, user: User, requested_master_id: int | None) -> User | None:
    if user.role == UserRole.MASTER:
        return user
    if not requested_master_id:
        return None
    return db.scalar(select(User).where(User.id == requested_master_id, User.role == UserRole.MASTER))


def apply_master_scope(query: Select, user: User, selected_master: User | None) -> Select:
    if user.role == UserRole.MASTER:
        return query.where(Appointment.master_id == user.id)
    if selected_master:
        return query.where(Appointment.master_id == selected_master.id)
    return query


def apply_period_scope(query: Select, column: object, value: str) -> Select:
    start, end = appointment_period_bounds(value)
    if start is not None:
        query = query.where(column >= start)
    if end is not None:
        query = query.where(column < end)
    return query


def calculate_master_prices(db: Session, master_id: int, service_ids: list[int]) -> PriceSummary:
    if not service_ids:
        return PriceSummary(total=0, items=[], missing_service_names=[])

    services = list(db.scalars(select(Service).where(Service.id.in_(service_ids)).order_by(Service.name.asc())))
    prices = list(
        db.scalars(
            select(MasterServicePrice).where(
                MasterServicePrice.master_id == master_id,
                MasterServicePrice.service_id.in_(service_ids),
            )
        )
    )

    price_map = {price.service_id: price.price for price in prices}
    items: list[dict[str, int | str]] = []
    missing: list[str] = []
    total = 0

    for service in services:
        value = price_map.get(service.id)
        if value is None:
            missing.append(service.name)
            continue
        total += value
        items.append({"service_id": service.id, "service_name": service.name, "price": value})

    return PriceSummary(total=total, items=items, missing_service_names=missing)


def calculate_appointment_price_map(db: Session, appointments: list[Appointment]) -> dict[int, PriceSummary]:
    if not appointments:
        return {}

    master_ids = {appointment.master_id for appointment in appointments}
    service_ids = {
        service.id
        for appointment in appointments
        for service in appointment.services
    }

    if not master_ids or not service_ids:
        return {
            appointment.id: PriceSummary(total=0, items=[], missing_service_names=[])
            for appointment in appointments
        }

    prices = list(
        db.scalars(
            select(MasterServicePrice).where(
                MasterServicePrice.master_id.in_(master_ids),
                MasterServicePrice.service_id.in_(service_ids),
            )
        )
    )
    price_map = {(price.master_id, price.service_id): price.price for price in prices}

    summaries: dict[int, PriceSummary] = {}
    for appointment in appointments:
        items: list[dict[str, int | str]] = []
        missing: list[str] = []
        total = 0
        for service in sorted(appointment.services, key=lambda item: item.name):
            value = price_map.get((appointment.master_id, service.id))
            if value is None:
                missing.append(service.name)
                continue
            total += value
            items.append({"service_id": service.id, "service_name": service.name, "price": value})
        summaries[appointment.id] = PriceSummary(total=total, items=items, missing_service_names=missing)

    return summaries


def sync_master_statuses(db: Session) -> int:
    now = datetime.now()
    recent_window_start = now - timedelta(minutes=30)
    updated = 0
    masters = list(db.scalars(select(User).where(User.role == UserRole.MASTER)))

    for master in masters:
        if master.availability_status == MasterAvailability.BUSY:
            continue

        changed_at = master.status_changed_at or datetime.min
        due_appointment_exists = db.scalar(
            select(
                exists().where(
                    Appointment.master_id == master.id,
                    Appointment.starts_at <= now,
                    Appointment.starts_at >= recent_window_start,
                    Appointment.starts_at > changed_at,
                    Appointment.status == AppointmentStatus.PENDING,
                )
            )
        )
        if due_appointment_exists:
            master.availability_status = MasterAvailability.BUSY
            master.status_changed_at = now
            updated += 1

    if updated:
        db.commit()
    return updated


def enforce_working_hours(start_value: datetime, open_time: time, close_time: time) -> bool:
    start_time = start_value.time()
    return open_time <= start_time < close_time
