"""处方查询与数据隔离（selectors）。

所有「按 id 取处方」的入口都必须经过这里，避免出现
「只要登录就能用 URL 遍历全院处方」的越权漏洞。
"""
from django.db.models import Case, IntegerField, Value, When
from django.shortcuts import get_object_or_404

from apps.prescriptions.models import Prescription
from apps.users.permissions import has_any_permission

# 拥有全量处方可见性的角色权限
FULL_ACCESS_PERMS = ("审核:读", "统计:读", "发药:确认", "收费:确认", "医务科:统计")

REVIEW_QUEUE_STATUSES = ["SUBMITTED", "PRECHECKED", "ALERTED", "REVIEWING", "FINAL_REVIEWING"]


def visible_prescriptions(user, include_deleted=False):
    """返回当前用户有权看到的处方集合。"""
    qs = Prescription.objects.all() if include_deleted else Prescription.objects.filter(is_deleted=False)

    if has_any_permission(user, *FULL_ACCESS_PERMS):
        return qs

    doctor = getattr(user, "doctor", None)
    if doctor:
        return qs.filter(doctor=doctor)

    patient = getattr(user, "patient_profile", None)
    if patient:
        return qs.filter(patient=patient)

    return qs.none()


def get_prescription_for(user, pk, include_deleted=False):
    """按 id 取处方，越权时返回 404（不泄露"该处方存在但无权访问"）。"""
    return get_object_or_404(visible_prescriptions(user, include_deleted), pk=pk)


def review_queue():
    """药师待审队列：预警拦截优先，其次按提交时间升序（数据库排序，不分页进内存）。"""
    return (
        Prescription.objects.filter(status__in=REVIEW_QUEUE_STATUSES, is_deleted=False)
        .select_related("patient", "doctor__user")
        .annotate(
            priority=Case(
                When(status="ALERTED", then=Value(0)),
                When(status="SUBMITTED", then=Value(1)),
                When(status="PRECHECKED", then=Value(2)),
                When(status="REVIEWING", then=Value(3)),
                When(status="FINAL_REVIEWING", then=Value(4)),
                default=Value(9),
                output_field=IntegerField(),
            )
        )
        .order_by("priority", "created_at")
    )


def dispense_queue():
    """药房发药队列：已审核通过且已缴费，等待发药。"""
    return (
        Prescription.objects.filter(
            status="APPROVED", pay_status="PAID", is_deleted=False
        )
        .select_related("patient", "doctor__user")
        .order_by("paid_at", "created_at")
    )


def unpaid_queue():
    """收费队列：已审核通过但未缴费。"""
    return (
        Prescription.objects.filter(
            status="APPROVED", pay_status="UNPAID", is_deleted=False
        )
        .select_related("patient", "doctor__user")
        .order_by("created_at")
    )
