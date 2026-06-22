from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List
from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    TECHNICIAN = "technician"
    REVIEWER = "reviewer"


class BatteryStatus(str, Enum):
    PENDING_CHARGE = "待充电"
    CHARGING = "充电中"
    PENDING_VERIFICATION = "待核验"
    READY_FOR_USE = "可装机"
    ABNORMAL_OBSERVATION = "异常观察"
    SUSPENDED = "暂停使用"
    SCRAPPED = "已报废"


class RiskLevel(str, Enum):
    LOW = "低风险"
    MEDIUM = "中风险"
    HIGH = "高风险"
    CRITICAL = "极高风险"


class Disposition(str, Enum):
    CONTINUE_USE = "继续使用"
    OBSERVATION = "观察使用"
    LIMITED_USE = "限制使用"
    SUSPEND = "暂停使用"
    SCRAP = "建议报废"


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    full_name: str = Field(..., min_length=2, max_length=100)
    role: UserRole


class UserCreate(UserBase):
    password: str = Field(..., min_length=6, max_length=100)


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None


class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class BatteryBase(BaseModel):
    battery_code: str = Field(..., max_length=50)
    capacity_level: str = Field(..., max_length=50)
    compatible_drone: str = Field(..., max_length=100)
    charging_cabinet: str = Field(..., max_length=50)
    cycle_threshold: int = Field(..., gt=0)
    responsible_group: str = Field(..., max_length=100)
    verification_cycle_days: int = Field(..., gt=0)
    initial_capacity: float = Field(..., gt=0)
    current_capacity: float = Field(..., gt=0)


class BatteryCreate(BatteryBase):
    pass


class BatteryUpdate(BaseModel):
    capacity_level: Optional[str] = None
    compatible_drone: Optional[str] = None
    charging_cabinet: Optional[str] = None
    cycle_threshold: Optional[int] = None
    responsible_group: Optional[str] = None
    verification_cycle_days: Optional[int] = None
    current_capacity: Optional[float] = None
    status: Optional[BatteryStatus] = None
    has_bulge: Optional[bool] = None
    bulge_description: Optional[str] = None
    deactivation_suggestion: Optional[str] = None


class BatteryResponse(BatteryBase):
    id: int
    current_cycles: int
    status: BatteryStatus
    has_bulge: bool
    bulge_description: Optional[str] = None
    deactivation_suggestion: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ChargeStart(BaseModel):
    battery_id: int
    start_voltage: Optional[float] = None
    start_temperature: Optional[float] = None


class ChargeComplete(BaseModel):
    record_id: int
    end_voltage: Optional[float] = None
    end_temperature: Optional[float] = None


class ChargeRecordResponse(BaseModel):
    id: int
    battery_id: int
    start_time: datetime
    end_time: Optional[datetime] = None
    start_voltage: Optional[float] = None
    end_voltage: Optional[float] = None
    start_temperature: Optional[float] = None
    end_temperature: Optional[float] = None
    is_active: bool
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True


class DischargeTestCreate(BaseModel):
    battery_id: int
    start_voltage: float
    end_voltage: float
    discharge_capacity: float
    temperature: Optional[float] = None
    duration_minutes: Optional[int] = None


class DischargeRecordResponse(BaseModel):
    id: int
    battery_id: int
    test_time: datetime
    start_voltage: float
    end_voltage: float
    discharge_capacity: float
    temperature: Optional[float] = None
    duration_minutes: Optional[int] = None
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True


class PreFlightCheckCreate(BaseModel):
    battery_id: int
    voltage: float
    temperature: float
    appearance_ok: bool
    connector_ok: bool
    firmware_ok: bool
    has_bulge: bool = False
    bulge_description: Optional[str] = None
    remarks: Optional[str] = None


class PreFlightCheckResponse(BaseModel):
    id: int
    battery_id: int
    check_time: datetime
    voltage: float
    temperature: float
    appearance_ok: bool
    connector_ok: bool
    firmware_ok: bool
    has_bulge: bool
    bulge_description: Optional[str] = None
    remarks: Optional[str] = None
    is_active: bool
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True


class TemperatureRecordCreate(BaseModel):
    battery_id: int
    temperature: float
    location: Optional[str] = None
    remarks: Optional[str] = None


class TemperatureRecordResponse(BaseModel):
    id: int
    battery_id: int
    record_time: datetime
    temperature: float
    location: Optional[str] = None
    remarks: Optional[str] = None
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True


class BulgeDescriptionUpdate(BaseModel):
    battery_id: int
    has_bulge: bool
    bulge_description: Optional[str] = None


class DeactivationSuggestionCreate(BaseModel):
    battery_id: int
    suggestion: str


class ReviewRecordCreate(BaseModel):
    battery_id: int
    retest_capacity: float
    risk_level: RiskLevel
    final_disposition: Disposition
    remarks: Optional[str] = None


class ReviewRecordResponse(BaseModel):
    id: int
    battery_id: int
    review_time: datetime
    retest_capacity: float
    risk_level: RiskLevel
    final_disposition: Disposition
    remarks: Optional[str] = None
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True


class HighRiskBattery(BaseModel):
    battery_id: int
    battery_code: str
    risk_score: float
    risk_factors: List[str]
    current_cycles: int
    status: str
    latest_risk_level: Optional[str] = None


class PendingVerificationItem(BaseModel):
    battery_id: int
    battery_code: str
    compatible_drone: str
    responsible_group: str
    status: str
    days_since_last_check: int
    last_check_time: Optional[datetime] = None


class CapacityTrendPoint(BaseModel):
    battery_id: int
    battery_code: str
    record_date: datetime
    capacity: float
    cycles: int


class ValidationWarning(BaseModel):
    type: str
    message: str
    battery_id: Optional[int] = None
    battery_code: Optional[str] = None
    details: Optional[str] = None
