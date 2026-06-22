from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    role = Column(String(20), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    charge_records_created = relationship(
        "ChargeRecord",
        foreign_keys="ChargeRecord.created_by",
        back_populates="creator"
    )
    discharge_records_created = relationship(
        "DischargeRecord",
        foreign_keys="DischargeRecord.created_by",
        back_populates="creator"
    )
    preflight_checks_created = relationship(
        "PreFlightCheck",
        foreign_keys="PreFlightCheck.created_by",
        back_populates="creator"
    )
    reviews_created = relationship(
        "ReviewRecord",
        foreign_keys="ReviewRecord.created_by",
        back_populates="creator"
    )


class Battery(Base):
    __tablename__ = "batteries"

    id = Column(Integer, primary_key=True, index=True)
    battery_code = Column(String(50), unique=True, index=True, nullable=False)
    capacity_level = Column(String(50), nullable=False)
    compatible_drone = Column(String(100), nullable=False)
    charging_cabinet = Column(String(50), nullable=False)
    cycle_threshold = Column(Integer, nullable=False)
    responsible_group = Column(String(100), nullable=False)
    verification_cycle_days = Column(Integer, nullable=False)
    current_cycles = Column(Integer, default=0)
    status = Column(String(30), default="待充电")
    initial_capacity = Column(Float, nullable=False)
    current_capacity = Column(Float, nullable=False)
    has_bulge = Column(Boolean, default=False)
    bulge_description = Column(Text, nullable=True)
    deactivation_suggestion = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    charge_records = relationship("ChargeRecord", back_populates="battery")
    discharge_records = relationship("DischargeRecord", back_populates="battery")
    preflight_checks = relationship("PreFlightCheck", back_populates="battery")
    temperature_records = relationship("TemperatureRecord", back_populates="battery")
    reviews = relationship("ReviewRecord", back_populates="battery")


class ChargeRecord(Base):
    __tablename__ = "charge_records"

    id = Column(Integer, primary_key=True, index=True)
    battery_id = Column(Integer, ForeignKey("batteries.id"), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=True)
    start_voltage = Column(Float, nullable=True)
    end_voltage = Column(Float, nullable=True)
    start_temperature = Column(Float, nullable=True)
    end_temperature = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    battery = relationship("Battery", back_populates="charge_records")
    creator = relationship("User", foreign_keys=[created_by], back_populates="charge_records_created")


class DischargeRecord(Base):
    __tablename__ = "discharge_records"

    id = Column(Integer, primary_key=True, index=True)
    battery_id = Column(Integer, ForeignKey("batteries.id"), nullable=False)
    test_time = Column(DateTime(timezone=True), nullable=False)
    start_voltage = Column(Float, nullable=False)
    end_voltage = Column(Float, nullable=False)
    discharge_capacity = Column(Float, nullable=False)
    temperature = Column(Float, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    battery = relationship("Battery", back_populates="discharge_records")
    creator = relationship("User", foreign_keys=[created_by], back_populates="discharge_records_created")


class PreFlightCheck(Base):
    __tablename__ = "preflight_checks"

    id = Column(Integer, primary_key=True, index=True)
    battery_id = Column(Integer, ForeignKey("batteries.id"), nullable=False)
    check_time = Column(DateTime(timezone=True), nullable=False)
    voltage = Column(Float, nullable=False)
    temperature = Column(Float, nullable=False)
    appearance_ok = Column(Boolean, nullable=False)
    connector_ok = Column(Boolean, nullable=False)
    firmware_ok = Column(Boolean, nullable=False)
    has_bulge = Column(Boolean, default=False)
    bulge_description = Column(Text, nullable=True)
    remarks = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    battery = relationship("Battery", back_populates="preflight_checks")
    creator = relationship("User", foreign_keys=[created_by], back_populates="preflight_checks_created")


class TemperatureRecord(Base):
    __tablename__ = "temperature_records"

    id = Column(Integer, primary_key=True, index=True)
    battery_id = Column(Integer, ForeignKey("batteries.id"), nullable=False)
    record_time = Column(DateTime(timezone=True), nullable=False)
    temperature = Column(Float, nullable=False)
    location = Column(String(100), nullable=True)
    remarks = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    battery = relationship("Battery", back_populates="temperature_records")


class ReviewRecord(Base):
    __tablename__ = "review_records"

    id = Column(Integer, primary_key=True, index=True)
    battery_id = Column(Integer, ForeignKey("batteries.id"), nullable=False)
    review_time = Column(DateTime(timezone=True), nullable=False)
    retest_capacity = Column(Float, nullable=False)
    risk_level = Column(String(20), nullable=False)
    final_disposition = Column(String(50), nullable=False)
    remarks = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    battery = relationship("Battery", back_populates="reviews")
    creator = relationship("User", foreign_keys=[created_by], back_populates="reviews_created")
