"""审核与发药业务服务层。

把「状态迁移 + 记录留痕 + 库存扣减 + 消息通知 + 操作日志」收在一个事务里，
视图只负责鉴权与返回，避免业务逻辑散落在多个 view 中导致事务边界错乱。
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.prescriptions.models import Herb, Prescription
from apps.reviews import engine, state_machine
from apps.reviews.models import DispenseRecord, Notification, ReviewRecord
from apps.users.models import User
from apps.users.permissions import get_client_ip

logger = logging.getLogger(__name__)

# 剂量单位换算：处方 dose 为 g，库存 stock 为 kg
GRAMS_PER_KG = Decimal("1000")


class BusinessError(Exception):
    """可预期的业务错误，视图层转成 4xx 并回显 message。"""

    def __init__(self, message, status=422):
        super().__init__(message)
        self.message = message
        self.status = status


def _notify(recipients, title, content, level="INFO"):
    """批量创建站内通知（一次 SQL，不循环 create）。"""
    if not recipients:
        return
    Notification.objects.bulk_create(
        [Notification(recipient=u, title=title, content=content, level=level) for u in recipients]
    )


def _pharmacist_users():
    return list(User.objects.filter(userrole__role__code__in=["pharmacist", "pharmacist_lead"]).distinct())


def notify_reviewers(prescription, blocked=False):
    """提交处方后提醒药师。"""
    _notify(
        _pharmacist_users(),
        "新处方待审核",
        f"处方 {prescription.presc_no} 已提交，请及时审核",
        "WARN" if blocked else "INFO",
    )


def log_action(request, action_type, object_type, object_id, detail, result="success"):
    from apps.stats.models import OperationLog

    OperationLog.objects.create(
        operator=request.user,
        action_type=action_type,
        object_type=object_type,
        object_id=str(object_id),
        detail=detail,
        ip=get_client_ip(request),
        result=result,
    )


def claim(prescription):
    """领取审核任务（条件更新，避免两人同时领取同一张处方）。"""
    ok, target = state_machine.transition(prescription, "claim")
    if not ok:
        raise BusinessError(target, status=409)
    return target


def approve(prescription, user, opinion="", alert_note="", adverse_note=""):
    """初审通过（进入复审）或复审通过（待缴费），并写审核记录。"""
    if prescription.status == "ALERTED" and not alert_note.strip():
        raise BusinessError("预警拦截处方放行必须填写异常备注")

    is_final = prescription.status == "FINAL_REVIEWING"
    pharmacist = getattr(user, "pharmacist", None)

    with transaction.atomic():
        if prescription.status == "ALERTED":
            ok, target = state_machine.transition(prescription, "release")
            if not ok:
                raise BusinessError(target, status=409)

        ok, target = state_machine.transition(prescription, "approve")
        if not ok:
            raise BusinessError(target, status=409)

        precheck = engine.precheck_cached(prescription)
        ReviewRecord.objects.create(
            prescription=prescription,
            review_level="FINAL" if is_final else "INITIAL",
            precheck_result=precheck.get("result_text", ""),
            review_opinion=opinion or ("复审通过" if is_final else "初审通过"),
            pharmacist=pharmacist,
            alert_note=alert_note,
            adverse_note=adverse_note,
        )

        if target == "APPROVED":
            _notify(
                [prescription.doctor.user],
                "处方审核通过",
                f"处方 {prescription.presc_no} 已通过审核，请患者完成缴费后取药",
                "INFO",
            )

    return target


def reject(prescription, user, opinion):
    """退回处方并记录理由。"""
    if not opinion.strip():
        raise BusinessError("退回必须填写退回理由")

    is_final = prescription.status == "FINAL_REVIEWING"

    with transaction.atomic():
        ok, target = state_machine.transition(prescription, "reject")
        if not ok:
            raise BusinessError(target, status=409)

        ReviewRecord.objects.create(
            prescription=prescription,
            review_level="FINAL" if is_final else "INITIAL",
            precheck_result="",
            review_opinion=f"退回：{opinion}",
            reject_reason=opinion,
            pharmacist=getattr(user, "pharmacist", None),
        )

        _notify(
            [prescription.doctor.user],
            "处方被退回",
            f"处方 {prescription.presc_no} 被退回：{opinion}",
            "WARN",
        )

    return target


def pay(prescription, user, amount=None):
    """缴费登记：把 pay_status 置为已缴费并留痕。

    这是「审核 → 缴费 → 发药」闭环中被漏掉的一环，
    没有它的话未缴费处方也能直接发药。
    """
    if prescription.pay_status == "PAID":
        raise BusinessError("该处方已完成缴费", status=409)
    if prescription.status != "APPROVED":
        raise BusinessError("仅审核通过的处方可以缴费", status=409)

    updated = Prescription.objects.filter(
        pk=prescription.pk, status="APPROVED", pay_status="UNPAID"
    ).update(pay_status="PAID", paid_at=timezone.now(), paid_by=user, updated_at=timezone.now())
    if not updated:
        raise BusinessError("缴费状态已被其他操作变更，请刷新", status=409)

    prescription.refresh_from_db()
    _notify(
        [prescription.doctor.user],
        "处方已缴费",
        f"处方 {prescription.presc_no} 已完成缴费，等待药房发药",
        "INFO",
    )
    return prescription


def check_stock(prescription):
    """发药前的库存校验，返回不足清单。"""
    items = list(prescription.items.select_related("herb"))
    doses = Decimal(max(prescription.total_doses or 1, 1))
    shortage = []
    for item in items:
        needed_kg = (Decimal(item.dose) * doses / GRAMS_PER_KG).quantize(Decimal("0.0001"))
        if Decimal(item.herb.stock) < needed_kg:
            shortage.append(
                {
                    "herb": item.herb.name,
                    "needed_kg": float(needed_kg),
                    "stock_kg": float(item.herb.stock),
                }
            )
    return shortage


def dispense(prescription, user, checked_herbs=True, note=""):
    """发药确认：校验已缴费 → 扣减库存 → 写发药记录 → 通知医师。"""
    if prescription.pay_status != "PAID":
        raise BusinessError("处方尚未缴费，不能发药", status=409)

    shortage = check_stock(prescription)
    if shortage:
        detail = "、".join(f"{s['herb']}（需 {s['needed_kg']}kg，存 {s['stock_kg']}kg）" for s in shortage)
        raise BusinessError(f"库存不足，无法发药：{detail}", status=409)

    doses = Decimal(max(prescription.total_doses or 1, 1))

    with transaction.atomic():
        # 锁行，避免同一处方被两路请求同时发药
        locked = Prescription.objects.select_for_update().get(pk=prescription.pk)
        ok, target = state_machine.transition(locked, "dispense")
        if not ok:
            raise BusinessError(target, status=409)

        for item in locked.items.select_related("herb"):
            deduct_kg = (Decimal(item.dose) * doses / GRAMS_PER_KG).quantize(Decimal("0.0001"))
            Herb.objects.filter(pk=item.herb_id).update(stock=F("stock") - deduct_kg)

        DispenseRecord.objects.create(
            prescription=locked,
            dispenser=user,
            checked_herbs=bool(checked_herbs),
            note=note[:200],
        )

        _notify(
            [locked.doctor.user],
            "处方已发药",
            f"处方 {locked.presc_no} 已完成发药",
            "INFO",
        )

    prescription.refresh_from_db()
    return target
