from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from enum import StrEnum

from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Enum
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import Text
from sqlalchemy import Time
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now()


class UserRole(StrEnum):
    DIRECTOR = "director"
    ADMIN = "admin"
    MASTER = "master"


class MasterAvailability(StrEnum):
    FREE = "free"
    BUSY = "busy"


class AppointmentStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BookingRequestStatus(StrEnum):
    NEW = "new"
    APPROVED = "approved"
    DECLINED = "declined"


AppointmentService = Table(
    "appointment_services",
    Base.metadata,
    Column("appointment_id", ForeignKey("appointments.id"), primary_key=True),
    Column("service_id", ForeignKey("services.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), index=True)
    avatar_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reminder_offsets: Mapped[str] = mapped_column(String(255), default="60", nullable=False)
    availability_status: Mapped[MasterAvailability] = mapped_column(
        Enum(MasterAvailability),
        default=MasterAvailability.FREE,
        nullable=False,
    )
    status_changed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    prices: Mapped[list[MasterServicePrice]] = relationship(
        back_populates="master",
        cascade="all, delete-orphan",
    )
    appointments: Mapped[list[Appointment]] = relationship(back_populates="master")


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    phone: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    avatar_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instagram: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    appointments: Mapped[list[Appointment]] = relationship(back_populates="client")


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    icon: Mapped[str] = mapped_column(String(32), default="✂", nullable=False)

    prices: Mapped[list[MasterServicePrice]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
    )
    appointments: Mapped[list[Appointment]] = relationship(
        secondary=AppointmentService,
        back_populates="services",
    )


class MasterServicePrice(Base):
    __tablename__ = "master_service_prices"
    __table_args__ = (UniqueConstraint("master_id", "service_id", name="uq_master_service_price"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), index=True)
    price: Mapped[int] = mapped_column(Integer)

    master: Mapped[User] = relationship(back_populates="prices")
    service: Mapped[Service] = relationship(back_populates="prices")


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True, index=True)
    guest_label: Mapped[str | None] = mapped_column(String(255), nullable=True, default="Гость")
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[AppointmentStatus] = mapped_column(Enum(AppointmentStatus), default=AppointmentStatus.PENDING, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    completion_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    client: Mapped[Client | None] = relationship(back_populates="appointments")
    master: Mapped[User] = relationship(back_populates="appointments")
    services: Mapped[list[Service]] = relationship(
        secondary=AppointmentService,
        back_populates="appointments",
    )
    receipt_photos: Mapped[list["AppointmentReceiptPhoto"]] = relationship(
        back_populates="appointment",
        cascade="all, delete-orphan",
        order_by="AppointmentReceiptPhoto.created_at.asc()",
    )
    prepayment_photos: Mapped[list["AppointmentPrepaymentPhoto"]] = relationship(
        back_populates="appointment",
        cascade="all, delete-orphan",
        order_by="AppointmentPrepaymentPhoto.created_at.asc()",
    )


class AppointmentReceiptPhoto(Base):
    __tablename__ = "appointment_receipt_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    file_path: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    appointment: Mapped[Appointment] = relationship(back_populates="receipt_photos")


class AppointmentPrepaymentPhoto(Base):
    __tablename__ = "appointment_prepayment_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    file_path: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    appointment: Mapped[Appointment] = relationship(back_populates="prepayment_photos")


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    salon_open_time: Mapped[time] = mapped_column(Time, default=time(10, 0), nullable=False)
    salon_close_time: Mapped[time] = mapped_column(Time, default=time(20, 0), nullable=False)


BookingRequestService = Table(
    "booking_request_services",
    Base.metadata,
    Column("booking_request_id", ForeignKey("booking_requests.id"), primary_key=True),
    Column("service_id", ForeignKey("services.id"), primary_key=True),
)


class BookingRequest(Base):
    __tablename__ = "booking_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    preferred_master_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    preferred_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    preferred_time: Mapped[time] = mapped_column(Time, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[BookingRequestStatus] = mapped_column(
        Enum(BookingRequestStatus),
        default=BookingRequestStatus.NEW,
        nullable=False,
        index=True,
    )
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id"), nullable=True
    )
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    preferred_master: Mapped[User | None] = relationship(
        foreign_keys=[preferred_master_id]
    )
    services: Mapped[list[Service]] = relationship(secondary=BookingRequestService)
    appointment: Mapped[Appointment | None] = relationship(foreign_keys=[appointment_id])
    resolved_by: Mapped[User | None] = relationship(foreign_keys=[resolved_by_user_id])
