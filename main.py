from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import Session

from database import engine, get_db, Base
from models import (
    User, Battery, ChargeRecord, DischargeRecord,
    PreFlightCheck, TemperatureRecord, ReviewRecord, AnomalyTicket
)
from schemas import (
    UserCreate, UserUpdate, UserResponse,
    BatteryCreate, BatteryUpdate, BatteryResponse,
    ChargeStart, ChargeComplete, ChargeRecordResponse,
    DischargeTestCreate, DischargeRecordResponse,
    PreFlightCheckCreate, PreFlightCheckResponse,
    TemperatureRecordCreate, TemperatureRecordResponse,
    BulgeDescriptionUpdate, DeactivationSuggestionCreate,
    ReviewRecordCreate, ReviewRecordResponse,
    HighRiskBattery, PendingVerificationItem,
    CapacityTrendPoint, Token, BatteryStatus, UserRole,
    AnomalySource, AnomalyTicketStatus, SiteDisposalCreate,
    AnomalyReviewCreate, AnomalyTicketResponse, AnomalyTicketStats,
    RiskLevel, Disposition
)
from auth import (
    authenticate_user, create_access_token, get_current_user,
    require_roles, get_password_hash, ACCESS_TOKEN_EXPIRE_MINUTES
)
from validators import (
    check_active_verification_conflict, check_cycle_threshold,
    check_consecutive_temperature_abnormal,
    check_cabinet_abnormal_concentration,
    check_preflight_check_missing, check_review_conclusion_missing,
    run_all_battery_validations, calculate_risk_score
)

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="无人机电池管理系统 API",
    description="低空巡检团队无人机电池循环、充放电记录和飞行前核验管理系统",
    version="1.0.0"
)


def init_default_admin(db: Session):
    admin = db.query(User).filter(User.username == "admin").first()
    if not admin:
        default_admin = User(
            username="admin",
            hashed_password=get_password_hash("admin123"),
            full_name="系统管理员",
            role=UserRole.ADMIN.value,
            is_active=True
        )
        db.add(default_admin)
        db.commit()


@app.on_event("startup")
def startup_event():
    db = next(get_db())
    init_default_admin(db)
    db.close()


def generate_ticket_no(db: Session) -> str:
    now = datetime.utcnow()
    prefix = f"ANOM{now.strftime('%Y%m%d')}"
    count = db.query(AnomalyTicket).filter(
        AnomalyTicket.ticket_no.like(f"{prefix}%")
    ).count() + 1
    return f"{prefix}{count:04d}"


def create_anomaly_ticket(
    db: Session,
    battery: Battery,
    anomaly_source: AnomalySource,
    trigger_reason: str,
    submitter_id: int
) -> Optional[AnomalyTicket]:
    open_ticket = db.query(AnomalyTicket).filter(
        and_(
            AnomalyTicket.battery_id == battery.id,
            AnomalyTicket.anomaly_source == anomaly_source.value,
            AnomalyTicket.status.in_([
                AnomalyTicketStatus.PENDING.value,
                AnomalyTicketStatus.DISPOSED.value
            ])
        )
    ).first()
    if open_ticket:
        return None
    ticket = AnomalyTicket(
        ticket_no=generate_ticket_no(db),
        battery_id=battery.id,
        anomaly_source=anomaly_source.value,
        trigger_reason=trigger_reason,
        battery_status_at_creation=battery.status,
        responsible_group=battery.responsible_group,
        submitter_id=submitter_id,
        status=AnomalyTicketStatus.PENDING.value
    )
    db.add(ticket)
    db.flush()
    return ticket


def format_ticket_response(ticket: AnomalyTicket, db: Session) -> dict:
    data = {
        "id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "battery_id": ticket.battery_id,
        "battery_code": ticket.battery.battery_code if ticket.battery else "",
        "anomaly_source": ticket.anomaly_source,
        "trigger_reason": ticket.trigger_reason,
        "battery_status_at_creation": ticket.battery_status_at_creation,
        "responsible_group": ticket.responsible_group,
        "submitter_id": ticket.submitter_id,
        "submitter_name": None,
        "status": ticket.status,
        "site_disposal_note": ticket.site_disposal_note,
        "disposed_by": ticket.disposed_by,
        "disposer_name": None,
        "disposed_at": ticket.disposed_at,
        "review_id": ticket.review_id,
        "disposition_conclusion": ticket.disposition_conclusion,
        "risk_level": ticket.risk_level,
        "retest_capacity": None,
        "final_disposition": ticket.final_disposition,
        "review_remark": ticket.review_remark,
        "reviewed_by": ticket.reviewed_by,
        "reviewer_name": None,
        "reviewed_at": ticket.reviewed_at,
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at
    }
    submitter = db.query(User).filter(User.id == ticket.submitter_id).first()
    if submitter:
        data["submitter_name"] = submitter.full_name
    if ticket.disposed_by:
        disposer = db.query(User).filter(User.id == ticket.disposed_by).first()
        if disposer:
            data["disposer_name"] = disposer.full_name
    if ticket.review_id:
        review = db.query(ReviewRecord).filter(ReviewRecord.id == ticket.review_id).first()
        if review:
            data["retest_capacity"] = review.retest_capacity
    if ticket.reviewed_by:
        reviewer = db.query(User).filter(User.id == ticket.reviewed_by).first()
        if reviewer:
            data["reviewer_name"] = reviewer.full_name
    return data


@app.post("/api/auth/login", response_model=Token, tags=["认证"])
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role},
        expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/api/auth/me", response_model=UserResponse, tags=["认证"])
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user


@app.post("/api/users", response_model=UserResponse, tags=["用户管理"])
def create_user(
    user: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN.value))
):
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="用户名已存在")
    hashed = get_password_hash(user.password)
    db_user = User(
        username=user.username,
        hashed_password=hashed,
        full_name=user.full_name,
        role=user.role.value
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


@app.get("/api/users", response_model=List[UserResponse], tags=["用户管理"])
def list_users(
    skip: int = 0,
    limit: int = 100,
    role: Optional[UserRole] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN.value))
):
    query = db.query(User)
    if role:
        query = query.filter(User.role == role.value)
    return query.offset(skip).limit(limit).all()


@app.get("/api/users/{user_id}", response_model=UserResponse, tags=["用户管理"])
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN.value))
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@app.put("/api/users/{user_id}", response_model=UserResponse, tags=["用户管理"])
def update_user(
    user_id: int,
    user_update: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN.value))
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    update_data = user_update.model_dump(exclude_unset=True)
    if "password" in update_data and update_data["password"]:
        update_data["hashed_password"] = get_password_hash(update_data.pop("password"))
    if "role" in update_data:
        update_data["role"] = update_data["role"].value
    for key, value in update_data.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


@app.post("/api/batteries", response_model=BatteryResponse, tags=["电池管理"])
def create_battery(
    battery: BatteryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN.value))
):
    existing = db.query(Battery).filter(Battery.battery_code == battery.battery_code).first()
    if existing:
        raise HTTPException(status_code=400, detail="电池编号已存在")
    db_battery = Battery(**battery.model_dump())
    db.add(db_battery)
    db.commit()
    db.refresh(db_battery)
    return db_battery


@app.get("/api/batteries", response_model=List[BatteryResponse], tags=["电池管理"])
def list_batteries(
    skip: int = 0,
    limit: int = 100,
    battery_code: Optional[str] = None,
    compatible_drone: Optional[str] = None,
    charging_cabinet: Optional[str] = None,
    responsible_group: Optional[str] = None,
    status: Optional[BatteryStatus] = None,
    risk_level: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(Battery)
    if battery_code:
        query = query.filter(Battery.battery_code.contains(battery_code))
    if compatible_drone:
        query = query.filter(Battery.compatible_drone.contains(compatible_drone))
    if charging_cabinet:
        query = query.filter(Battery.charging_cabinet.contains(charging_cabinet))
    if responsible_group:
        query = query.filter(Battery.responsible_group.contains(responsible_group))
    if status:
        query = query.filter(Battery.status == status.value)
    if date_from:
        query = query.filter(Battery.created_at >= date_from)
    if date_to:
        query = query.filter(Battery.created_at <= date_to)
    if risk_level:
        from sqlalchemy import func as sa_func
        latest_review_subq = db.query(
            ReviewRecord.battery_id,
            sa_func.max(ReviewRecord.review_time).label("latest_time")
        ).group_by(ReviewRecord.battery_id).subquery()
        subquery = db.query(ReviewRecord.battery_id).join(
            latest_review_subq,
            and_(
                ReviewRecord.battery_id == latest_review_subq.c.battery_id,
                ReviewRecord.review_time == latest_review_subq.c.latest_time
            )
        ).filter(ReviewRecord.risk_level == risk_level)
        query = query.filter(Battery.id.in_(subquery))
    return query.order_by(Battery.id.desc()).offset(skip).limit(limit).all()


@app.get("/api/batteries/{battery_id}", tags=["电池管理"])
def get_battery(
    battery_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    battery = db.query(Battery).filter(Battery.id == battery_id).first()
    if not battery:
        raise HTTPException(status_code=404, detail="电池不存在")
    anomaly_tickets_raw = db.query(AnomalyTicket).filter(
        AnomalyTicket.battery_id == battery_id
    ).order_by(AnomalyTicket.created_at.desc()).all()
    anomaly_tickets = [
        format_ticket_response(t, db) for t in anomaly_tickets_raw
    ]
    return {
        "battery": battery,
        "anomaly_tickets": anomaly_tickets
    }


@app.put("/api/batteries/{battery_id}", response_model=BatteryResponse, tags=["电池管理"])
def update_battery(
    battery_id: int,
    battery_update: BatteryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN.value))
):
    battery = db.query(Battery).filter(Battery.id == battery_id).first()
    if not battery:
        raise HTTPException(status_code=404, detail="电池不存在")
    update_data = battery_update.model_dump(exclude_unset=True)
    if "status" in update_data:
        update_data["status"] = update_data["status"].value
    for key, value in update_data.items():
        setattr(battery, key, value)
    battery.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(battery)
    return battery


@app.post("/api/technician/charge/start", response_model=ChargeRecordResponse, tags=["机务员功能"])
def start_charge(
    charge_data: ChargeStart,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.TECHNICIAN.value, UserRole.ADMIN.value))
):
    battery = db.query(Battery).filter(Battery.id == charge_data.battery_id).first()
    if not battery:
        raise HTTPException(status_code=404, detail="电池不存在")
    if battery.status in [BatteryStatus.SCRAPPED.value, BatteryStatus.SUSPENDED.value]:
        raise HTTPException(status_code=400, detail=f"电池状态为 {battery.status}，无法充电")
    active_charge = db.query(ChargeRecord).filter(
        and_(
            ChargeRecord.battery_id == charge_data.battery_id,
            ChargeRecord.is_active == True
        )
    ).first()
    if active_charge:
        raise HTTPException(status_code=400, detail="该电池已有进行中的充电记录")
    record = ChargeRecord(
        battery_id=charge_data.battery_id,
        start_time=datetime.utcnow(),
        start_voltage=charge_data.start_voltage,
        start_temperature=charge_data.start_temperature,
        is_active=True,
        created_by=current_user.id
    )
    battery.status = BatteryStatus.CHARGING.value
    battery.updated_at = datetime.utcnow()
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@app.post("/api/technician/charge/complete", response_model=ChargeRecordResponse, tags=["机务员功能"])
def complete_charge(
    charge_data: ChargeComplete,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.TECHNICIAN.value, UserRole.ADMIN.value))
):
    record = db.query(ChargeRecord).filter(
        and_(ChargeRecord.id == charge_data.record_id, ChargeRecord.is_active == True)
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="未找到进行中的充电记录")
    record.end_time = datetime.utcnow()
    record.end_voltage = charge_data.end_voltage
    record.end_temperature = charge_data.end_temperature
    record.is_active = False
    battery = db.query(Battery).filter(Battery.id == record.battery_id).first()
    if battery:
        battery.status = BatteryStatus.PENDING_VERIFICATION.value
        battery.current_cycles += 1
        battery.updated_at = datetime.utcnow()
        cycle_exceeded, cycle_msg = check_cycle_threshold(db, battery)
        if cycle_exceeded:
            battery.status = BatteryStatus.ABNORMAL_OBSERVATION.value
            create_anomaly_ticket(
                db, battery, AnomalySource.CYCLE_EXCEEDED,
                cycle_msg,
                current_user.id
            )
    db.commit()
    db.refresh(record)
    return record


@app.post("/api/technician/discharge", response_model=DischargeRecordResponse, tags=["机务员功能"])
def record_discharge_test(
    discharge_data: DischargeTestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.TECHNICIAN.value, UserRole.ADMIN.value))
):
    battery = db.query(Battery).filter(Battery.id == discharge_data.battery_id).first()
    if not battery:
        raise HTTPException(status_code=404, detail="电池不存在")
    record = DischargeRecord(
        battery_id=discharge_data.battery_id,
        test_time=datetime.utcnow(),
        start_voltage=discharge_data.start_voltage,
        end_voltage=discharge_data.end_voltage,
        discharge_capacity=discharge_data.discharge_capacity,
        temperature=discharge_data.temperature,
        duration_minutes=discharge_data.duration_minutes,
        cycles_at_test=battery.current_cycles,
        created_by=current_user.id
    )
    battery.current_capacity = discharge_data.discharge_capacity
    battery.updated_at = datetime.utcnow()
    capacity_ratio = battery.current_capacity / battery.initial_capacity if battery.initial_capacity > 0 else 0
    if capacity_ratio < 0.8:
        battery.status = BatteryStatus.ABNORMAL_OBSERVATION.value
        create_anomaly_ticket(
            db, battery, AnomalySource.CAPACITY_DECAY,
            f"容量衰减至 {capacity_ratio:.1%}（当前 {battery.current_capacity}mAh / 初始 {battery.initial_capacity}mAh），低于安全阈值 80%",
            current_user.id
        )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@app.post("/api/technician/preflight-check", response_model=PreFlightCheckResponse, tags=["机务员功能"])
def create_preflight_check(
    check_data: PreFlightCheckCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.TECHNICIAN.value, UserRole.ADMIN.value))
):
    battery = db.query(Battery).filter(Battery.id == check_data.battery_id).first()
    if not battery:
        raise HTTPException(status_code=404, detail="电池不存在")
    db.query(PreFlightCheck).filter(
        and_(
            PreFlightCheck.battery_id == check_data.battery_id,
            PreFlightCheck.is_active == True
        )
    ).update({"is_active": False})
    db.flush()
    all_checks_ok = (
        check_data.appearance_ok and check_data.connector_ok and
        check_data.firmware_ok and not check_data.has_bulge
    )
    record = PreFlightCheck(
        battery_id=check_data.battery_id,
        check_time=datetime.utcnow(),
        voltage=check_data.voltage,
        temperature=check_data.temperature,
        appearance_ok=check_data.appearance_ok,
        connector_ok=check_data.connector_ok,
        firmware_ok=check_data.firmware_ok,
        has_bulge=check_data.has_bulge,
        bulge_description=check_data.bulge_description,
        remarks=check_data.remarks,
        is_active=True,
        created_by=current_user.id
    )
    if check_data.has_bulge:
        battery.has_bulge = True
        battery.bulge_description = check_data.bulge_description
        create_anomaly_ticket(
            db, battery, AnomalySource.BULGE,
            f"飞前核验发现电池鼓包：{check_data.bulge_description or '外观鼓胀'}",
            current_user.id
        )
    if all_checks_ok:
        battery.status = BatteryStatus.READY_FOR_USE.value
    else:
        battery.status = BatteryStatus.ABNORMAL_OBSERVATION.value
        fail_reasons = []
        if not check_data.appearance_ok:
            fail_reasons.append("外观检查不通过")
        if not check_data.connector_ok:
            fail_reasons.append("接口检查不通过")
        if not check_data.firmware_ok:
            fail_reasons.append("固件检查不通过")
        if check_data.has_bulge:
            fail_reasons.append("检测到鼓包")
        if not check_data.has_bulge and fail_reasons:
            create_anomaly_ticket(
                db, battery, AnomalySource.PREFLIGHT_FAIL,
                f"飞前核验不通过：{'; '.join(fail_reasons)}",
                current_user.id
            )
        elif check_data.has_bulge and fail_reasons:
            non_bulge_reasons = [r for r in fail_reasons if r != "检测到鼓包"]
            if non_bulge_reasons:
                create_anomaly_ticket(
                    db, battery, AnomalySource.PREFLIGHT_FAIL,
                    f"飞前核验不通过：{'; '.join(non_bulge_reasons)}",
                    current_user.id
                )
    battery.updated_at = datetime.utcnow()
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@app.post("/api/technician/temperature", response_model=TemperatureRecordResponse, tags=["机务员功能"])
def record_temperature(
    temp_data: TemperatureRecordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.TECHNICIAN.value, UserRole.ADMIN.value))
):
    battery = db.query(Battery).filter(Battery.id == temp_data.battery_id).first()
    if not battery:
        raise HTTPException(status_code=404, detail="电池不存在")
    record = TemperatureRecord(
        battery_id=temp_data.battery_id,
        record_time=datetime.utcnow(),
        temperature=temp_data.temperature,
        location=temp_data.location,
        remarks=temp_data.remarks,
        created_by=current_user.id
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    is_abnormal, temp_msg = check_consecutive_temperature_abnormal(db, temp_data.battery_id)
    if is_abnormal and temp_msg:
        battery.status = BatteryStatus.ABNORMAL_OBSERVATION.value
        battery.updated_at = datetime.utcnow()
        create_anomaly_ticket(
            db, battery, AnomalySource.TEMP_ABNORMAL,
            temp_msg,
            current_user.id
        )
        db.commit()
    return record


@app.post("/api/technician/bulge", tags=["机务员功能"])
def update_bulge_description(
    data: BulgeDescriptionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.TECHNICIAN.value, UserRole.ADMIN.value))
):
    battery = db.query(Battery).filter(Battery.id == data.battery_id).first()
    if not battery:
        raise HTTPException(status_code=404, detail="电池不存在")
    battery.has_bulge = data.has_bulge
    battery.bulge_description = data.bulge_description
    if data.has_bulge:
        battery.status = BatteryStatus.ABNORMAL_OBSERVATION.value
        create_anomaly_ticket(
            db, battery, AnomalySource.BULGE,
            f"机务员上报电池鼓包：{data.bulge_description or '外观鼓胀'}",
            current_user.id
        )
    battery.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(battery)
    return {"message": "鼓包信息已更新", "battery_code": battery.battery_code}


@app.post("/api/technician/deactivation-suggestion", tags=["机务员功能"])
def submit_deactivation_suggestion(
    data: DeactivationSuggestionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.TECHNICIAN.value, UserRole.ADMIN.value))
):
    battery = db.query(Battery).filter(Battery.id == data.battery_id).first()
    if not battery:
        raise HTTPException(status_code=404, detail="电池不存在")
    battery.deactivation_suggestion = data.suggestion
    battery.status = BatteryStatus.ABNORMAL_OBSERVATION.value
    create_anomaly_ticket(
        db, battery, AnomalySource.DEACTIVATION_SUGGESTION,
        f"机务员提交停用建议：{data.suggestion}",
        current_user.id
    )
    battery.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "停用建议已提交", "battery_code": battery.battery_code}


@app.post("/api/reviewer/review", response_model=ReviewRecordResponse, tags=["复核员功能"])
def create_review_record(
    review_data: ReviewRecordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.REVIEWER.value, UserRole.ADMIN.value))
):
    battery = db.query(Battery).filter(Battery.id == review_data.battery_id).first()
    if not battery:
        raise HTTPException(status_code=404, detail="电池不存在")
    record = ReviewRecord(
        battery_id=review_data.battery_id,
        review_time=datetime.utcnow(),
        retest_capacity=review_data.retest_capacity,
        risk_level=review_data.risk_level.value,
        final_disposition=review_data.final_disposition.value,
        remarks=review_data.remarks,
        created_by=current_user.id
    )
    battery.current_capacity = review_data.retest_capacity
    if review_data.final_disposition.value == "继续使用":
        battery.status = BatteryStatus.READY_FOR_USE.value
    elif review_data.final_disposition.value == "观察使用":
        battery.status = BatteryStatus.ABNORMAL_OBSERVATION.value
    elif review_data.final_disposition.value == "限制使用":
        battery.status = BatteryStatus.ABNORMAL_OBSERVATION.value
    elif review_data.final_disposition.value == "暂停使用":
        battery.status = BatteryStatus.SUSPENDED.value
    elif review_data.final_disposition.value == "建议报废":
        battery.status = BatteryStatus.SCRAPPED.value
    battery.updated_at = datetime.utcnow()
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@app.get("/api/stats/high-risk-ranking", response_model=List[HighRiskBattery], tags=["统计查询"])
def get_high_risk_ranking(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    batteries = db.query(Battery).filter(
        Battery.status != BatteryStatus.SCRAPPED.value
    ).all()
    ranked = []
    for battery in batteries:
        score, factors = calculate_risk_score(db, battery)
        if score > 0:
            latest_review = db.query(ReviewRecord).filter(
                ReviewRecord.battery_id == battery.id
            ).order_by(ReviewRecord.review_time.desc()).first()
            ranked.append(HighRiskBattery(
                battery_id=battery.id,
                battery_code=battery.battery_code,
                risk_score=score,
                risk_factors=factors,
                current_cycles=battery.current_cycles,
                status=battery.status,
                latest_risk_level=latest_review.risk_level if latest_review else None
            ))
    ranked.sort(key=lambda x: x.risk_score, reverse=True)
    return ranked[:limit]


@app.get("/api/stats/pending-verification", response_model=List[PendingVerificationItem], tags=["统计查询"])
def get_pending_verification_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    batteries = db.query(Battery).filter(
        Battery.status.notin_([
            BatteryStatus.SCRAPPED.value,
            BatteryStatus.SUSPENDED.value
        ])
    ).all()
    pending = []
    for battery in batteries:
        latest_check = db.query(PreFlightCheck).filter(
            PreFlightCheck.battery_id == battery.id
        ).order_by(PreFlightCheck.check_time.desc()).first()
        if latest_check:
            days_since = (datetime.utcnow() - latest_check.check_time).days
        else:
            days_since = 9999
        if (not latest_check) or days_since > battery.verification_cycle_days or battery.status == BatteryStatus.PENDING_VERIFICATION.value:
            pending.append(PendingVerificationItem(
                battery_id=battery.id,
                battery_code=battery.battery_code,
                compatible_drone=battery.compatible_drone,
                responsible_group=battery.responsible_group,
                status=battery.status,
                days_since_last_check=days_since,
                last_check_time=latest_check.check_time if latest_check else None
            ))
    pending.sort(key=lambda x: x.days_since_last_check, reverse=True)
    return pending


@app.get("/api/stats/capacity-trend", response_model=List[CapacityTrendPoint], tags=["统计查询"])
def get_capacity_trend(
    battery_id: Optional[int] = None,
    days: int = 90,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(DischargeRecord)
    if battery_id:
        query = query.filter(DischargeRecord.battery_id == battery_id)
    date_cutoff = datetime.utcnow() - timedelta(days=days)
    records = query.filter(
        DischargeRecord.test_time >= date_cutoff
    ).order_by(DischargeRecord.test_time.asc()).all()
    trend_points = []
    for record in records:
        battery = db.query(Battery).filter(Battery.id == record.battery_id).first()
        if battery:
            trend_points.append(CapacityTrendPoint(
                battery_id=record.battery_id,
                battery_code=battery.battery_code,
                record_date=record.test_time,
                capacity=record.discharge_capacity,
                cycles=record.cycles_at_test
            ))
    return trend_points


@app.get("/api/batteries/{battery_id}/validations", tags=["统计查询"])
def get_battery_validations(
    battery_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    battery = db.query(Battery).filter(Battery.id == battery_id).first()
    if not battery:
        raise HTTPException(status_code=404, detail="电池不存在")
    warnings = run_all_battery_validations(db, battery)
    return {"battery_code": battery.battery_code, "warnings": warnings}


@app.get("/api/batteries/{battery_id}/records", tags=["统计查询"])
def get_battery_full_history(
    battery_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    battery = db.query(Battery).filter(Battery.id == battery_id).first()
    if not battery:
        raise HTTPException(status_code=404, detail="电池不存在")
    charges = db.query(ChargeRecord).filter(
        ChargeRecord.battery_id == battery_id
    ).order_by(ChargeRecord.start_time.desc()).all()
    discharges = db.query(DischargeRecord).filter(
        DischargeRecord.battery_id == battery_id
    ).order_by(DischargeRecord.test_time.desc()).all()
    preflights = db.query(PreFlightCheck).filter(
        PreFlightCheck.battery_id == battery_id
    ).order_by(PreFlightCheck.check_time.desc()).all()
    temperatures = db.query(TemperatureRecord).filter(
        TemperatureRecord.battery_id == battery_id
    ).order_by(TemperatureRecord.record_time.desc()).all()
    reviews = db.query(ReviewRecord).filter(
        ReviewRecord.battery_id == battery_id
    ).order_by(ReviewRecord.review_time.desc()).all()
    anomaly_tickets_raw = db.query(AnomalyTicket).filter(
        AnomalyTicket.battery_id == battery_id
    ).order_by(AnomalyTicket.created_at.desc()).all()
    anomaly_tickets = [
        format_ticket_response(t, db) for t in anomaly_tickets_raw
    ]
    return {
        "battery": battery,
        "charge_records": charges,
        "discharge_records": discharges,
        "preflight_checks": preflights,
        "temperature_records": temperatures,
        "review_records": reviews,
        "anomaly_tickets": anomaly_tickets
    }


@app.post("/api/technician/anomaly/dispose", tags=["机务员功能"])
def dispose_anomaly_ticket(
    data: SiteDisposalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.TECHNICIAN.value, UserRole.ADMIN.value))
):
    ticket = db.query(AnomalyTicket).filter(AnomalyTicket.id == data.ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="异常工单不存在")
    if ticket.status != AnomalyTicketStatus.PENDING.value:
        raise HTTPException(status_code=400, detail=f"工单状态为 [{ticket.status}]，无法处置")
    ticket.site_disposal_note = data.site_disposal_note
    ticket.disposed_by = current_user.id
    ticket.disposed_at = datetime.utcnow()
    ticket.status = AnomalyTicketStatus.DISPOSED.value
    ticket.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(ticket)
    return format_ticket_response(ticket, db)


@app.post("/api/reviewer/anomaly/review", tags=["复核员功能"])
def review_anomaly_ticket(
    data: AnomalyReviewCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.REVIEWER.value, UserRole.ADMIN.value))
):
    ticket = db.query(AnomalyTicket).filter(AnomalyTicket.id == data.ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="异常工单不存在")
    if ticket.status != AnomalyTicketStatus.DISPOSED.value:
        raise HTTPException(status_code=400, detail=f"工单状态为 [{ticket.status}]，无法复核，请先完成现场处置")
    battery = db.query(Battery).filter(Battery.id == ticket.battery_id).first()
    if not battery:
        raise HTTPException(status_code=404, detail="关联电池不存在")
    review_record = ReviewRecord(
        battery_id=ticket.battery_id,
        review_time=datetime.utcnow(),
        retest_capacity=data.retest_capacity,
        risk_level=data.risk_level.value,
        final_disposition=data.final_disposition.value,
        remarks=data.review_remark or "",
        created_by=current_user.id
    )
    db.add(review_record)
    db.flush()
    ticket.review_id = review_record.id
    ticket.disposition_conclusion = data.disposition_conclusion
    ticket.risk_level = data.risk_level.value
    ticket.final_disposition = data.final_disposition.value
    ticket.review_remark = data.review_remark
    ticket.reviewed_by = current_user.id
    ticket.reviewed_at = datetime.utcnow()
    ticket.status = AnomalyTicketStatus.COMPLETED.value
    ticket.updated_at = datetime.utcnow()
    battery.current_capacity = data.retest_capacity
    if data.final_disposition == Disposition.CONTINUE_USE:
        battery.status = BatteryStatus.READY_FOR_USE.value
    elif data.final_disposition == Disposition.OBSERVATION:
        battery.status = BatteryStatus.ABNORMAL_OBSERVATION.value
    elif data.final_disposition == Disposition.LIMITED_USE:
        battery.status = BatteryStatus.ABNORMAL_OBSERVATION.value
    elif data.final_disposition == Disposition.SUSPEND:
        battery.status = BatteryStatus.SUSPENDED.value
    elif data.final_disposition == Disposition.SCRAP:
        battery.status = BatteryStatus.SCRAPPED.value
    battery.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(ticket)
    return format_ticket_response(ticket, db)


@app.get("/api/anomaly/tickets", response_model=List[dict], tags=["异常处置"])
def list_anomaly_tickets(
    skip: int = 0,
    limit: int = 100,
    battery_code: Optional[str] = None,
    responsible_group: Optional[str] = None,
    status: Optional[AnomalyTicketStatus] = None,
    risk_level: Optional[RiskLevel] = None,
    anomaly_source: Optional[AnomalySource] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(AnomalyTicket).join(Battery, AnomalyTicket.battery_id == Battery.id)
    if battery_code:
        query = query.filter(Battery.battery_code.contains(battery_code))
    if responsible_group:
        query = query.filter(AnomalyTicket.responsible_group.contains(responsible_group))
    if status:
        query = query.filter(AnomalyTicket.status == status.value)
    if risk_level:
        query = query.filter(AnomalyTicket.risk_level == risk_level.value)
    if anomaly_source:
        query = query.filter(AnomalyTicket.anomaly_source == anomaly_source.value)
    if date_from:
        query = query.filter(AnomalyTicket.created_at >= date_from)
    if date_to:
        query = query.filter(AnomalyTicket.created_at <= date_to)
    tickets = query.order_by(AnomalyTicket.created_at.desc()).offset(skip).limit(limit).all()
    return [format_ticket_response(t, db) for t in tickets]


@app.get("/api/anomaly/tickets/{ticket_id}", tags=["异常处置"])
def get_anomaly_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    ticket = db.query(AnomalyTicket).filter(AnomalyTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="异常工单不存在")
    return format_ticket_response(ticket, db)


@app.get("/api/anomaly/stats", response_model=AnomalyTicketStats, tags=["异常处置"])
def get_anomaly_stats(
    responsible_group: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = db.query(AnomalyTicket)
    if responsible_group:
        query = query.filter(AnomalyTicket.responsible_group.contains(responsible_group))
    if date_from:
        query = query.filter(AnomalyTicket.created_at >= date_from)
    if date_to:
        query = query.filter(AnomalyTicket.created_at <= date_to)
    all_tickets = query.all()
    pending_count = sum(1 for t in all_tickets if t.status == AnomalyTicketStatus.PENDING.value)
    disposed_count = sum(1 for t in all_tickets if t.status == AnomalyTicketStatus.DISPOSED.value)
    completed_count = sum(1 for t in all_tickets if t.status == AnomalyTicketStatus.COMPLETED.value)
    cancelled_count = sum(1 for t in all_tickets if t.status == AnomalyTicketStatus.CANCELLED.value)
    risk_level_counts = {
        RiskLevel.LOW.value: 0,
        RiskLevel.MEDIUM.value: 0,
        RiskLevel.HIGH.value: 0,
        RiskLevel.CRITICAL.value: 0,
        "未评定": 0
    }
    for t in all_tickets:
        if t.risk_level:
            if t.risk_level in risk_level_counts:
                risk_level_counts[t.risk_level] += 1
        else:
            risk_level_counts["未评定"] += 1
    return AnomalyTicketStats(
        pending_count=pending_count,
        disposed_count=disposed_count,
        completed_count=completed_count,
        cancelled_count=cancelled_count,
        risk_level_counts=risk_level_counts
    )
