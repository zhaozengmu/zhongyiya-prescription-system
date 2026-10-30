"""处方审核管理视图。

药师工作台：待审队列、领取、初审/复审、退回、发药、轨迹与消息通知。
业务动作统一委托给 ``services``，本模块只做鉴权、参数校验与响应组装。
"""
import logging

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from apps.prescriptions.selectors import get_prescription_for, review_queue
from apps.reviews import engine, services
from apps.reviews.models import Notification
from apps.reviews.services import BusinessError
from apps.users.permissions import deny, has_permission, parse_json_body

logger = logging.getLogger(__name__)

PAGE_SIZE = 15


@login_required
def review_workbench(request):
    """药师工作台：待审队列（预警拦截优先，数据库排序 + 分页）。"""
    if not has_permission(request.user, "审核:读"):
        return deny(request, perm_code="审核:读")

    queue = review_queue()
    # 统计必须基于整个队列，不能只看当前页，否则翻页后数字会跳动
    stats = {
        "total": queue.count(),
        "alerted": queue.filter(status="ALERTED").count(),
        "initial": queue.filter(status__in=("PRECHECKED", "REVIEWING")).count(),
        "final": queue.filter(status="FINAL_REVIEWING").count(),
    }
    page = Paginator(queue, PAGE_SIZE).get_page(request.GET.get("page", 1))
    return render(request, "reviews/workbench.html", {
        "page": page,
        "queue": page.object_list,
        "stats": stats,
        "can_final_review": has_permission(request.user, "审核:复审"),
    })


@login_required
def review_detail(request, pk):
    """审核详情：预审命中规则 + 初审/复审操作。"""
    if not has_permission(request.user, "审核:读"):
        return deny(request, perm_code="审核:读")
    prescription = get_prescription_for(request.user, pk)

    items = list(prescription.items.select_related("herb"))
    precheck = engine.precheck_cached(prescription, items=items)
    history = prescription.review_records.select_related("pharmacist__user").order_by("created_at")

    return render(request, "reviews/review_detail.html", {
        "prescription": prescription,
        "items": items,
        "precheck": precheck,
        "hit_stats": engine.hit_stats(precheck.get("hits", [])),
        "history": history,
        "can_final_review": has_permission(request.user, "审核:复审"),
        "can_reject": has_permission(request.user, "审核:退回"),
        # 只有这些状态才需要（且允许）人工审核操作；其余状态展示"已完结"提示
        "is_actionable": prescription.status in ("PRECHECKED", "ALERTED", "REVIEWING", "FINAL_REVIEWING"),
    })


@login_required
def notifications_view(request):
    """我的消息通知。"""
    page = Paginator(request.user.notifications.all(), 20).get_page(request.GET.get("page", 1))
    return render(request, "reviews/notifications.html", {"page": page, "notifications": page.object_list})


# ============ API 接口 ============

@require_GET
def api_review_tasks(request):
    """GET /api/v1/reviews/tasks 药师待审队列。"""
    if not request.user.is_authenticated:
        return JsonResponse({"code": 401, "message": "未登录"}, status=401)
    if not has_permission(request.user, "审核:读"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    items = [
        {
            "id": p.id,
            "presc_no": p.presc_no,
            "patient_name": p.patient.name,
            "doctor_name": p.doctor.user.get_full_name() or p.doctor.user.username,
            "status": p.status,
            "status_display": p.get_status_display(),
            "created_at": p.created_at.isoformat(),
        }
        for p in review_queue()
    ]
    return JsonResponse({"code": 200, "message": "ok", "data": {"items": items, "total": len(items)}})


@require_GET
def api_review_detail(request, pk):
    """GET /api/v1/reviews/{id} 审核详情与命中规则。"""
    if not request.user.is_authenticated:
        return JsonResponse({"code": 401, "message": "未登录"}, status=401)
    if not has_permission(request.user, "审核:读"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    prescription = get_prescription_for(request.user, pk)
    items = list(
        prescription.items.select_related("herb").values("herb__name", "dose", "unit", "decoction", "usage")
    )
    return JsonResponse({
        "code": 200,
        "message": "ok",
        "data": {
            "id": prescription.id,
            "presc_no": prescription.presc_no,
            "diagnosis": prescription.diagnosis,
            "syndrome": prescription.syndrome,
            "status": prescription.status,
            "pay_status": prescription.pay_status,
            "items": items,
            "precheck": engine.precheck_cached(prescription),
        },
    })


@require_POST
def api_review_claim(request, pk):
    """POST /api/v1/reviews/{id}/claim 领取审核任务。"""
    if not has_permission(request.user, "审核:领取"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    prescription = get_prescription_for(request.user, pk)
    try:
        target = services.claim(prescription)
    except BusinessError as exc:
        return JsonResponse({"code": exc.status, "message": exc.message}, status=exc.status)

    services.log_action(request, "REVIEW_CLAIM", "prescription", pk, f"领取 {prescription.presc_no}")
    return JsonResponse({"code": 200, "message": "领取成功", "data": {"status": target}})


@require_POST
def api_review_approve(request, pk):
    """POST /api/v1/reviews/{id}/approve 初审通过（进入复审）或复审通过（待缴费）。"""
    data, error = parse_json_body(request)
    if error:
        return error

    prescription = get_prescription_for(request.user, pk)
    is_final = prescription.status == "FINAL_REVIEWING"
    required_perm = "审核:复审" if is_final else "审核:通过"
    if not has_permission(request.user, required_perm):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    try:
        target = services.approve(
            prescription,
            request.user,
            opinion=(data.get("opinion") or "").strip(),
            alert_note=(data.get("alert_note") or "").strip(),
            adverse_note=(data.get("adverse_note") or "").strip(),
        )
    except BusinessError as exc:
        return JsonResponse({"code": exc.status, "message": exc.message}, status=exc.status)

    services.log_action(
        request, "REVIEW_APPROVE", "prescription", pk,
        f"{'复审' if is_final else '初审'}通过 {prescription.presc_no}",
    )
    msg = "复审通过，等待患者缴费" if is_final else "初审通过，进入复审"
    return JsonResponse({"code": 200, "message": msg, "data": {"status": target}})


@require_POST
def api_review_reject(request, pk):
    """POST /api/v1/reviews/{id}/reject 退回并记录理由。"""
    if not has_permission(request.user, "审核:退回"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    data, error = parse_json_body(request)
    if error:
        return error

    prescription = get_prescription_for(request.user, pk)
    opinion = (data.get("opinion") or "").strip()

    try:
        target = services.reject(prescription, request.user, opinion)
    except BusinessError as exc:
        return JsonResponse({"code": exc.status, "message": exc.message}, status=exc.status)

    services.log_action(request, "REVIEW_REJECT", "prescription", pk, f"退回 {prescription.presc_no}：{opinion}")
    return JsonResponse({"code": 200, "message": "已退回", "data": {"status": target}})


@require_GET
def api_review_history(request, pk):
    """GET /api/v1/reviews/{id}/history 查看审核轨迹。"""
    if not request.user.is_authenticated:
        return JsonResponse({"code": 401, "message": "未登录"}, status=401)

    prescription = get_prescription_for(request.user, pk)
    history = list(
        prescription.review_records.all()
        .values(
            "id", "review_level", "precheck_result", "review_opinion", "reject_reason",
            "adverse_note", "pharmacist__user__username", "created_at",
        )
        .order_by("created_at")
    )
    return JsonResponse({"code": 200, "message": "ok", "data": history})


@require_POST
def api_dispense(request, pk):
    """POST /api/v1/reviews/{id}/dispense 药房发药确认。"""
    if not has_permission(request.user, "发药:确认"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    data, error = parse_json_body(request)
    if error:
        return error

    prescription = get_prescription_for(request.user, pk)
    try:
        target = services.dispense(
            prescription,
            request.user,
            checked_herbs=data.get("checked_herbs", True),
            note=(data.get("note") or "").strip(),
        )
    except BusinessError as exc:
        return JsonResponse({"code": exc.status, "message": exc.message}, status=exc.status)

    services.log_action(request, "DISPENSE", "prescription", pk, f"发药 {prescription.presc_no}")
    return JsonResponse({"code": 200, "message": "发药完成", "data": {"status": target}})


@require_GET
def api_notifications(request):
    """GET /api/v1/notifications 我的消息通知列表。"""
    if not request.user.is_authenticated:
        return JsonResponse({"code": 401, "message": "未登录"}, status=401)
    notifications = request.user.notifications.all()[:50]
    items = [
        {
            "id": n.id, "title": n.title, "content": n.content,
            "level": n.level, "is_read": n.is_read,
            "created_at": n.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for n in notifications
    ]
    unread = request.user.notifications.filter(is_read=False).count()
    return JsonResponse({"code": 200, "message": "ok", "data": {"items": items, "unread": unread}})


@require_POST
def api_notification_read(request, pk):
    """POST /api/v1/notifications/{id}/read 标记已读。"""
    if not request.user.is_authenticated:
        return JsonResponse({"code": 401, "message": "未登录"}, status=401)
    updated = Notification.objects.filter(pk=pk, recipient=request.user).update(is_read=True)
    if not updated:
        return JsonResponse({"code": 404, "message": "通知不存在"}, status=404)
    return JsonResponse({"code": 200, "message": "ok", "data": None})
