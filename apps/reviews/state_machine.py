"""处方审核状态机。

状态迁移规则（含初审/复审两级审核）：
草稿 -> 提交 -> 待预审
待预审 -> 系统预审 -> 已预审 / 预警拦截
已预审 -> 药师领取 -> 初审中
预警拦截 -> 填写理由放行 -> 初审中
初审中 -> 初审通过 -> 复审中 / 退回 -> 已驳回
复审中 -> 复审通过 -> 已通过待缴费 / 退回 -> 已驳回
已驳回 -> 修改后重新提交 -> 待预审（版本号+1）
已通过待缴费 -> 缴费 -> 已缴费（pay_status=PAID）
已缴费 -> 发药确认 -> 已发药完结（终态）

并发控制：迁移用「带状态条件的 UPDATE」实现 CAS，
两个药师同时点通过时只有一个能成功，另一个收到"状态已被变更"。
"""
from django.db.models import F
from django.utils import timezone

from apps.prescriptions.models import Prescription

# 允许的状态迁移映射：当前状态 -> 触发事件 -> 目标状态
TRANSITIONS = {
    "DRAFT": {"submit": "SUBMITTED"},
    "SUBMITTED": {"precheck_pass": "PRECHECKED", "precheck_block": "ALERTED"},
    "PRECHECKED": {"claim": "REVIEWING", "reject": "REJECTED"},
    "ALERTED": {"release": "REVIEWING", "reject": "REJECTED"},
    # 初审通过 -> 复审中；初审退回 -> 已驳回
    "REVIEWING": {"approve": "FINAL_REVIEWING", "reject": "REJECTED"},
    # 复审通过 -> 待缴费；复审退回 -> 已驳回
    "FINAL_REVIEWING": {"approve": "APPROVED", "reject": "REJECTED"},
    "REJECTED": {"resubmit": "SUBMITTED"},
    # 缴费通过后 pay_status 置为 PAID，状态仍停在 APPROVED，由发药环节校验
    "APPROVED": {"dispense": "DISPENSED"},
    "DISPENSED": {},  # 终态
}

EVENT_LABELS = {
    "submit": "提交预审",
    "precheck_pass": "预审通过",
    "precheck_block": "预审拦截",
    "claim": "领取任务",
    "release": "预警放行",
    "approve": "审核通过",
    "reject": "退回",
    "resubmit": "重新提交",
    "dispense": "确认发药",
}


class StateConflict(Exception):
    """状态迁移被并发请求抢先，调用方应提示用户刷新。"""


def can(prescription, event):
    """当前状态是否允许某事件。"""
    return event in TRANSITIONS.get(prescription.status, {})


def available_events(prescription):
    """当前状态可触发的全部事件。"""
    return list(TRANSITIONS.get(prescription.status, {}).keys())


def transition(prescription, event, **extra_fields):
    """执行状态迁移（CAS）。

    返回 (success, target_or_error)。
    非法迁移或并发冲突返回 (False, 原因)，绝不静默覆盖别人的修改。
    """
    current = prescription.status
    allowed = TRANSITIONS.get(current, {})
    if event not in allowed:
        return False, f"非法状态迁移：{current} 状态下不允许 {event}"

    target = allowed[event]
    updates = {"status": target, "updated_at": timezone.now(), **extra_fields}
    if event == "resubmit":
        updates["version"] = F("version") + 1  # 重新提交版本号加一

    updated = Prescription.objects.filter(pk=prescription.pk, status=current, is_deleted=False).update(**updates)
    if not updated:
        return False, "状态已被其他操作变更，请刷新后重试"

    prescription.refresh_from_db()
    return True, target
