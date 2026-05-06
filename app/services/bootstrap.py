from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta

from sqlalchemy import inspect
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import get_password_hash
from app.models.entities import Appointment
from app.models.entities import AppointmentReceiptPhoto
from app.models.entities import AppointmentStatus
from app.models.entities import Client
from app.models.entities import MasterAvailability
from app.models.entities import MasterServicePrice
from app.models.entities import Service
from app.models.entities import Setting
from app.models.entities import User
from app.models.entities import UserRole
from app.models.entities import utc_now


MASTER_AVATARS = {
    "master.nika": "https://i.pravatar.cc/300?img=32",
    "master.aliya": "https://i.pravatar.cc/300?img=47",
}

CLIENT_AVATARS = {
    "+998900000001": "https://i.pravatar.cc/300?img=21",
    "+998900000002": "https://i.pravatar.cc/300?img=16",
    "+998900000003": "https://i.pravatar.cc/300?img=49",
}

DEFAULT_SERVICE_ICONS = {
    "Стрижка": "✂",
    "Окрашивание": "🎨",
    "Маникюр": "💅",
    "Укладка": "✨",
    "Педикюр": "🦶",
    "Макияж": "💄",
    "Чистка": "🫧",
    "Эпиляция": "🪒",
}
LOCAL_TIME_MIGRATION_VERSION = 1


def _convert_legacy_utc_naive_to_local(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    local_tz = datetime.now().astimezone().tzinfo
    if local_tz is None:
        return value
    return value.replace(tzinfo=UTC).astimezone(local_tz).replace(tzinfo=None)


def migrate_legacy_utc_timestamps(db: Session) -> None:
    bind = db.bind
    if bind is None or bind.dialect.name != "sqlite":
        return

    current_version = db.execute(text("PRAGMA user_version")).scalar() or 0
    if current_version >= LOCAL_TIME_MIGRATION_VERSION:
        return

    for user in db.scalars(select(User)).all():
        user.created_at = _convert_legacy_utc_naive_to_local(user.created_at)
        user.status_changed_at = _convert_legacy_utc_naive_to_local(user.status_changed_at)

    for client in db.scalars(select(Client)).all():
        client.created_at = _convert_legacy_utc_naive_to_local(client.created_at)

    for appointment in db.scalars(select(Appointment)).all():
        appointment.created_at = _convert_legacy_utc_naive_to_local(appointment.created_at)
        appointment.completed_at = _convert_legacy_utc_naive_to_local(appointment.completed_at)

    for receipt in db.scalars(select(AppointmentReceiptPhoto)).all():
        receipt.created_at = _convert_legacy_utc_naive_to_local(receipt.created_at)

    db.execute(text(f"PRAGMA user_version = {LOCAL_TIME_MIGRATION_VERSION}"))
    db.commit()


def run_startup_migrations(db: Session) -> None:
    inspector = inspect(db.bind)
    table_names = set(inspector.get_table_names())

    if "users" in table_names:
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        if "status_changed_at" not in user_columns:
            db.execute(text("ALTER TABLE users ADD COLUMN status_changed_at DATETIME"))
            db.execute(text("UPDATE users SET status_changed_at = CURRENT_TIMESTAMP WHERE status_changed_at IS NULL"))
            db.commit()
        if "reminder_offsets" not in user_columns:
            db.execute(text("ALTER TABLE users ADD COLUMN reminder_offsets VARCHAR(255)"))
            db.execute(text("UPDATE users SET reminder_offsets = '60' WHERE role = 'MASTER' AND (reminder_offsets IS NULL OR trim(reminder_offsets) = '')"))
            db.execute(text("UPDATE users SET reminder_offsets = '' WHERE role != 'MASTER' AND reminder_offsets IS NULL"))
            db.commit()

    if "clients" in table_names:
        client_columns = {column["name"] for column in inspector.get_columns("clients")}
        if "avatar_path" not in client_columns:
            db.execute(text("ALTER TABLE clients ADD COLUMN avatar_path VARCHAR(255)"))
            db.commit()

    if "appointments" in table_names:
        appointment_columns = {column["name"] for column in inspector.get_columns("appointments")}
        if "status" not in appointment_columns:
            db.execute(text("ALTER TABLE appointments ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'PENDING'"))
        if "completion_comment" not in appointment_columns:
            db.execute(text("ALTER TABLE appointments ADD COLUMN completion_comment TEXT"))
        if "completed_at" not in appointment_columns:
            db.execute(text("ALTER TABLE appointments ADD COLUMN completed_at DATETIME"))
        db.execute(text("UPDATE appointments SET status = 'PENDING' WHERE status IS NULL OR lower(status) = 'pending'"))
        db.execute(text("UPDATE appointments SET status = 'COMPLETED' WHERE lower(status) = 'completed'"))
        db.execute(text("UPDATE appointments SET status = 'CANCELLED' WHERE lower(status) = 'cancelled'"))
        db.execute(text("UPDATE appointments SET completed_at = created_at WHERE status = 'COMPLETED' AND completed_at IS NULL"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_appointments_status ON appointments(status)"))
        db.commit()

    if "services" in table_names:
        service_columns = {column["name"] for column in inspector.get_columns("services")}
        if "icon" not in service_columns:
            db.execute(text("ALTER TABLE services ADD COLUMN icon VARCHAR(32)"))
            for service_name, service_icon in DEFAULT_SERVICE_ICONS.items():
                db.execute(
                    text("UPDATE services SET icon = :icon WHERE name = :name AND (icon IS NULL OR trim(icon) = '')"),
                    {"name": service_name, "icon": service_icon},
                )
            db.execute(text("UPDATE services SET icon = '✂' WHERE icon IS NULL OR trim(icon) = ''"))
            db.commit()

    migrate_legacy_utc_timestamps(db)


def bootstrap_defaults(db: Session) -> None:
    settings = get_settings()
    run_startup_migrations(db)

    director_exists = db.scalar(select(User).where(User.username == settings.bootstrap_director_login))
    if not director_exists:
        db.add(
            User(
                username=settings.bootstrap_director_login,
                password_hash=get_password_hash(settings.bootstrap_director_password),
                full_name=settings.bootstrap_director_full_name,
                role=UserRole.DIRECTOR,
                status_changed_at=utc_now(),
            )
        )

    salon_settings = db.scalar(select(Setting))
    if not salon_settings:
        db.add(Setting(salon_open_time=time(10, 0), salon_close_time=time(20, 0)))

    db.commit()
    if settings.seed_demo_data:
        seed_demo_data(db)


def seed_demo_data(db: Session) -> None:
    users_by_username = {user.username: user for user in db.scalars(select(User).order_by(User.id.asc()))}

    if "admin" not in users_by_username:
        db.add(
            User(
                username="admin",
                password_hash=get_password_hash("admin123"),
                full_name="Администратор салона",
                role=UserRole.ADMIN,
                status_changed_at=utc_now(),
            )
        )
    if "master.nika" not in users_by_username:
        db.add(
            User(
                username="master.nika",
                password_hash=get_password_hash("master123"),
                full_name="Ника Айдарова",
                role=UserRole.MASTER,
                availability_status=MasterAvailability.FREE,
                status_changed_at=utc_now(),
            )
        )
    if "master.aliya" not in users_by_username:
        db.add(
            User(
                username="master.aliya",
                password_hash=get_password_hash("master123"),
                full_name="Алия Садыкова",
                role=UserRole.MASTER,
                availability_status=MasterAvailability.FREE,
                status_changed_at=utc_now(),
            )
        )
    if "admin" not in users_by_username or "master.nika" not in users_by_username or "master.aliya" not in users_by_username:
        db.commit()

    users_by_username = {user.username: user for user in db.scalars(select(User).order_by(User.id.asc()))}
    for username, avatar_url in MASTER_AVATARS.items():
        user = users_by_username.get(username)
        if user and not user.avatar_path:
            user.avatar_path = avatar_url
    db.commit()

    services = list(db.scalars(select(Service)))
    if not services:
        db.add_all(
            [
                Service(name="Стрижка", icon="✂"),
                Service(name="Окрашивание", icon="🎨"),
                Service(name="Маникюр", icon="💅"),
                Service(name="Укладка", icon="✨"),
            ]
        )
        db.commit()
    else:
        for service in services:
            if not service.icon or not service.icon.strip():
                service.icon = DEFAULT_SERVICE_ICONS.get(service.name, "✂")
        db.commit()

    clients = list(db.scalars(select(Client).order_by(Client.id.asc())))
    if not clients:
        db.add_all(
            [
                Client(
                    full_name="Айгерим Турсунова",
                    phone="+998900000001",
                    instagram="@aigerim",
                    avatar_path=CLIENT_AVATARS["+998900000001"],
                ),
                Client(
                    full_name="Марина Орлова",
                    phone="+998900000002",
                    instagram="@marina_nails",
                    avatar_path=CLIENT_AVATARS["+998900000002"],
                ),
                Client(
                    full_name="София Ким",
                    phone="+998900000003",
                    instagram="@sofiakim",
                    avatar_path=CLIENT_AVATARS["+998900000003"],
                ),
            ]
        )
        db.commit()

    clients = list(db.scalars(select(Client).order_by(Client.id.asc())))
    for client in clients:
        avatar_url = CLIENT_AVATARS.get(client.phone) or f"https://i.pravatar.cc/300?img={60 + (client.id % 10)}"
        if not client.avatar_path:
            client.avatar_path = avatar_url
    db.commit()

    masters = list(db.scalars(select(User).where(User.role == UserRole.MASTER).order_by(User.id.asc())))
    services = list(db.scalars(select(Service).order_by(Service.id.asc())))
    if masters and services and not db.scalar(select(MasterServicePrice)):
        base_prices = {
            services[0].name: (1200, 1500),
            services[1].name: (4500, 5200),
            services[2].name: (1800, 2100),
            services[3].name: (900, 1100),
        }
        for index, master in enumerate(masters):
            for service in services:
                db.add(
                    MasterServicePrice(
                        master_id=master.id,
                        service_id=service.id,
                        price=base_prices.get(service.name, (1000, 1200))[index % 2],
                    )
                )
        db.commit()

    if not db.scalar(select(Appointment)):
        clients = list(db.scalars(select(Client).order_by(Client.id.asc())))
        today = date.today()
        appointments = [
            Appointment(
                client_id=clients[0].id,
                master_id=masters[0].id,
                starts_at=datetime.combine(today, time(11, 0)),
                status=AppointmentStatus.PENDING,
                comment="Клиент просил сохранить длину.",
                services=[services[0], services[3]],
            ),
            Appointment(
                client_id=clients[1].id,
                master_id=masters[1].id,
                starts_at=datetime.combine(today, time(14, 0)),
                status=AppointmentStatus.PENDING,
                comment="Новый оттенок по референсу из Instagram.",
                services=[services[1]],
            ),
            Appointment(
                client_id=None,
                guest_label="Гость",
                master_id=masters[0].id,
                starts_at=datetime.combine(today + timedelta(days=1), time(16, 0)),
                status=AppointmentStatus.PENDING,
                comment="Гостевой визит без карточки клиента.",
                services=[services[2]],
            ),
        ]
        db.add_all(appointments)
        db.commit()
