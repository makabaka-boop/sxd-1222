from datetime import datetime, timedelta
from typing import List, Tuple
from sqlalchemy import and_, func
from sqlalchemy.orm import Session
from models import (
    Battery, ChargeRecord, PreFlightCheck, TemperatureRecord,
    ReviewRecord, DischargeRecord
)
from schemas import (
    BatteryStatus, ValidationWarning
)

TEMPERATURE_THRESHOLD_HIGH = 45.0
TEMPERATURE_THRESHOLD_LOW = 0.0
CONSECUTIVE_TEMP_ABNORMAL_COUNT = 3
CABINET_ABNORMAL_RATIO = 0.3
CABINET_MIN_BATTERIES = 3


def check_active_verification_conflict(db: Session, battery_id: int) -> bool:
    active_check = db.query(PreFlightCheck).filter(
        and_(
            PreFlightCheck.battery_id == battery_id,
            PreFlightCheck.is_active == True
        )
    ).first()
    return active_check is not None


def check_cycle_threshold(db: Session, battery: Battery) -> Tuple[bool, str]:
    if battery.current_cycles > battery.cycle_threshold:
        return True, f"循环次数 {battery.current_cycles} 超过阈值 {battery.cycle_threshold}"
    return False, ""


def check_consecutive_temperature_abnormal(db: Session, battery_id: int) -> Tuple[bool, str]:
    recent_records = db.query(TemperatureRecord).filter(
        TemperatureRecord.battery_id == battery_id
    ).order_by(TemperatureRecord.record_time.desc()).limit(CONSECUTIVE_TEMP_ABNORMAL_COUNT).all()

    if len(recent_records) < CONSECUTIVE_TEMP_ABNORMAL_COUNT:
        return False, ""

    abnormal_count = 0
    for record in recent_records:
        if record.temperature > TEMPERATURE_THRESHOLD_HIGH or record.temperature < TEMPERATURE_THRESHOLD_LOW:
            abnormal_count += 1

    if abnormal_count == CONSECUTIVE_TEMP_ABNORMAL_COUNT:
        temps = [f"{r.temperature}°C" for r in recent_records]
        return True, f"连续 {CONSECUTIVE_TEMP_ABNORMAL_COUNT} 次温度异常: {', '.join(temps)}"
    return False, ""


def check_cabinet_abnormal_concentration(db: Session, cabinet: str) -> Tuple[bool, str]:
    batteries_in_cabinet = db.query(Battery).filter(
        Battery.charging_cabinet == cabinet
    ).all()

    if len(batteries_in_cabinet) < CABINET_MIN_BATTERIES:
        return False, ""

    abnormal_statuses = [
        BatteryStatus.ABNORMAL_OBSERVATION.value,
        BatteryStatus.SUSPENDED.value,
        BatteryStatus.SCRAPPED.value
    ]

    abnormal_count = sum(
        1 for b in batteries_in_cabinet if b.status in abnormal_statuses or b.has_bulge
    )
    ratio = abnormal_count / len(batteries_in_cabinet)

    if ratio >= CABINET_ABNORMAL_RATIO:
        return True, (
            f"柜位 {cabinet} 异常比例 {ratio:.1%} "
            f"({abnormal_count}/{len(batteries_in_cabinet)}) 超过阈值 {CABINET_ABNORMAL_RATIO:.0%}"
        )
    return False, ""


def check_preflight_check_missing(db: Session, battery: Battery) -> Tuple[bool, str]:
    if battery.status in [
        BatteryStatus.SCRAPPED.value,
        BatteryStatus.SUSPENDED.value
    ]:
        return False, ""

    latest_check = db.query(PreFlightCheck).filter(
        PreFlightCheck.battery_id == battery.id
    ).order_by(PreFlightCheck.check_time.desc()).first()

    if not latest_check:
        return True, "从未进行过飞行前核验"

    days_since = (datetime.utcnow() - latest_check.check_time).days
    if days_since > battery.verification_cycle_days:
        return True, (
            f"飞行前核验已过期 {days_since} 天 "
            f"(核验周期 {battery.verification_cycle_days} 天)"
        )
    return False, ""


def check_review_conclusion_missing(db: Session, battery: Battery) -> Tuple[bool, str]:
    if battery.status not in [
        BatteryStatus.ABNORMAL_OBSERVATION.value,
        BatteryStatus.PENDING_VERIFICATION.value
    ]:
        return False, ""

    abnormal_indicators = [
        battery.has_bulge,
        battery.current_cycles > battery.cycle_threshold,
        battery.deactivation_suggestion is not None
    ]

    if not any(abnormal_indicators):
        return False, ""

    latest_review = db.query(ReviewRecord).filter(
        ReviewRecord.battery_id == battery.id
    ).order_by(ReviewRecord.review_time.desc()).first()

    if not latest_review:
        return True, "存在异常指标但缺少复核结论"

    days_since = (datetime.utcnow() - latest_review.review_time).days
    if days_since > 7:
        return True, f"复核结论已超过 7 天未更新（{days_since} 天）"

    return False, ""


def run_all_battery_validations(db: Session, battery: Battery) -> List[ValidationWarning]:
    warnings: List[ValidationWarning] = []

    cycle_alert, cycle_msg = check_cycle_threshold(db, battery)
    if cycle_alert:
        warnings.append(ValidationWarning(
            type="cycle_threshold_exceeded",
            message=cycle_msg,
            battery_id=battery.id,
            battery_code=battery.battery_code
        ))

    temp_alert, temp_msg = check_consecutive_temperature_abnormal(db, battery.id)
    if temp_alert:
        warnings.append(ValidationWarning(
            type="consecutive_temperature_abnormal",
            message=temp_msg,
            battery_id=battery.id,
            battery_code=battery.battery_code
        ))

    cabinet_alert, cabinet_msg = check_cabinet_abnormal_concentration(
        db, battery.charging_cabinet
    )
    if cabinet_alert:
        warnings.append(ValidationWarning(
            type="cabinet_abnormal_concentration",
            message=cabinet_msg,
            battery_id=battery.id,
            battery_code=battery.battery_code,
            details=battery.charging_cabinet
        ))

    pf_alert, pf_msg = check_preflight_check_missing(db, battery)
    if pf_alert:
        warnings.append(ValidationWarning(
            type="preflight_check_missing",
            message=pf_msg,
            battery_id=battery.id,
            battery_code=battery.battery_code
        ))

    review_alert, review_msg = check_review_conclusion_missing(db, battery)
    if review_alert:
        warnings.append(ValidationWarning(
            type="review_conclusion_missing",
            message=review_msg,
            battery_id=battery.id,
            battery_code=battery.battery_code
        ))

    return warnings


def calculate_risk_score(db: Session, battery: Battery) -> Tuple[float, List[str]]:
    score = 0.0
    factors: List[str] = []

    if battery.current_cycles > battery.cycle_threshold:
        excess = battery.current_cycles - battery.cycle_threshold
        score += min(excess * 2, 40)
        factors.append(f"循环超阈值 {excess} 次")

    if battery.has_bulge:
        score += 30
        factors.append("电池鼓包")

    temp_alert, _ = check_consecutive_temperature_abnormal(db, battery.id)
    if temp_alert:
        score += 25
        factors.append("连续温度异常")

    pf_alert, _ = check_preflight_check_missing(db, battery)
    if pf_alert:
        score += 10
        factors.append("飞行前核验缺失/过期")

    if battery.status == BatteryStatus.ABNORMAL_OBSERVATION.value:
        score += 15
        factors.append("处于异常观察状态")
    elif battery.status == BatteryStatus.SUSPENDED.value:
        score += 20
        factors.append("已暂停使用")
    elif battery.status == BatteryStatus.SCRAPPED.value:
        score += 50
        factors.append("已报废")

    capacity_ratio = battery.current_capacity / battery.initial_capacity if battery.initial_capacity > 0 else 0
    if capacity_ratio < 0.7:
        score += 20
        factors.append(f"容量衰减至 {capacity_ratio:.1%}")
    elif capacity_ratio < 0.8:
        score += 10
        factors.append(f"容量衰减至 {capacity_ratio:.1%}")

    latest_review = db.query(ReviewRecord).filter(
        ReviewRecord.battery_id == battery.id
    ).order_by(ReviewRecord.review_time.desc()).first()
    if latest_review:
        if latest_review.risk_level == "高风险":
            score += 20
            factors.append("复核评定高风险")
        elif latest_review.risk_level == "极高风险":
            score += 40
            factors.append("复核评定极高风险")

    return min(score, 100), factors
