"""
Modelos do banco de dados (tabelas SQLAlchemy).
Separados dos schemas Pydantic que ficam em app/models/.
"""
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Enum as SAEnum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base
from app.models.ride import RideStatus
from app.models.driver import DriverStatus


class DriverORM(Base):
    __tablename__ = "drivers"

    id:              Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name:            Mapped[str] = mapped_column(String, nullable=False)
    license_plate:   Mapped[str] = mapped_column(String, nullable=False)
    phone:           Mapped[str] = mapped_column(String, nullable=False)
    status:          Mapped[str] = mapped_column(String, default=DriverStatus.AVAILABLE)
    current_ride_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at:      Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PassengerORM(Base):
    __tablename__ = "passengers"

    id:         Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name:       Mapped[str] = mapped_column(String, nullable=False)
    phone:      Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RideORM(Base):
    __tablename__ = "rides"

    id:               Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    passenger_id:     Mapped[str] = mapped_column(String, nullable=False)
    origin:           Mapped[str] = mapped_column(String, nullable=False)
    destination:      Mapped[str] = mapped_column(String, nullable=False)
    status:           Mapped[str] = mapped_column(String, default=RideStatus.REQUEST)
    driver_id:        Mapped[str | None] = mapped_column(String, nullable=True)
    delegated_to:     Mapped[str | None] = mapped_column(String, nullable=True)
    delegated_from:   Mapped[str | None] = mapped_column(String, nullable=True)
    created_at:       Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:       Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
