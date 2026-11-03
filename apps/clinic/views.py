"""门诊接诊、电子病历与病案服务视图。

权限边界：
- 医师只能操作「自己名下」的挂号与病历；
- 医务科/管理员可只读全量，便于质控抽查；
- 患者只能查看本人就诊记录、发起本人病案申请。
"""
import random
import string
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.patients.models import Patient
from apps.stats.models import OperationLog
from apps.users.models import Doctor
from apps.users.permissions import deny, get_client_ip, has_any_permission, has_permission

from .models import MedicalRecord, MedicalRecordRequest, Registration

PAGE_SIZE = 20
TIME_SLOTS = [
    "上午 08:00-10:00",
    "上午 10:00-12:00",
    "下午 14:00-16:00",
    "下午 16:00-18:00",
    "夜间 18:00-20:00",
]


def _log(request, action_type, object_type, object_id, detail, result="success"):
    OperationLog.objects.create(
        operator=request.user,
        action_type=action_type,
        object_type=object_type,
        object_id=str(object_id),
        detail=detail,
        ip=get_client_ip(request),
        result=result,
    )


def _int(raw, default=None):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _date(raw, default=None):
    if not raw:
        return default
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return default


def _my_patient(user):
    return getattr(user, "patient_profile", None)


def _my_doctor(user):
    return getattr(user, "doctor", None)


def _can_view_all(user):
    """医务科/管理员可查看全量门诊数据（只读）。"""
    return (
        has_any_permission(user, "医务科:统计", "用户:审核")
        or user.is_superuser
    )


def _doctor_scope(request, doctor):
    """医师看本人，管理角色看全量；两者都没有则返回空集。"""
    if doctor and not _can_view_all(request.user):
        return {"doctor": doctor}
    if _can_view_all(request.user):
        return {}
    return None


# ============ 医师工作台与接诊 ============

@login_required
def workbench(request):
    """医师诊室工作台：今日号源、待接诊、病历与病案待办。"""
    if not has_permission(request.user, "接诊:读"):
        return deny(request, "仅医师或门诊管理岗位可进入诊室工作台", perm_code="接诊:读")

    doctor = _my_doctor(request.user)
    scope = _doctor_scope(request, doctor)
    if scope is None:
        return deny(request, "当前账号未绑定医师档案", perm_code="接诊:读")

    visit_date = _date(request.GET.get("date"), timezone.localdate())
    regs = (
        Registration.objects.filter(visit_date=visit_date, **scope)
        .select_related("patient", "doctor__user")
        .order_by("queue_no")
    )
    counts = {
        "waiting": regs.filter(status="WAITING").count(),
        "in_progress": regs.filter(status="IN_PROGRESS").count(),
        "finished": regs.filter(status="FINISHED").count(),
        "total": regs.count(),
    }
    records = (
        MedicalRecord.objects.filter(registration__visit_date=visit_date, **scope)
        .select_related("patient")
        .order_by("-created_at")[:8]
    )

    pending_requests = []
    can_handle_request = has_permission(request.user, "病历申请:处理")
    if can_handle_request:
        pending_requests = (
            MedicalRecordRequest.objects.filter(status="PENDING")
            .select_related("patient")
            .order_by("created_at")[:6]
        )

    return render(request, "clinic/workbench.html", {
        "visit_date": visit_date,
        "regs": regs,
        "counts": counts,
        "records": records,
        "pending_requests": pending_requests,
        "can_handle_request": can_handle_request,
        "doctor": doctor,
        "is_manager_view": doctor is None,
        "prev_date": visit_date - timedelta(days=1),
        "next_date": visit_date + timedelta(days=1),
        "today": timezone.localdate(),
    })


@login_required
def registration_list(request):
    """挂号与接诊队列。"""
    if not has_any_permission(request.user, "接诊:读", "挂号:管理"):
        return deny(request, "无挂号或接诊权限", perm_code="挂号:管理")

    doctor = _my_doctor(request.user)
    scope = _doctor_scope(request, doctor)
    if scope is None:
        scope = {}

    qs = Registration.objects.select_related("patient", "doctor__user").filter(**scope)
    visit_date = _date(request.GET.get("date"), timezone.localdate())
    status = request.GET.get("status", "").strip()
    keyword = request.GET.get("name", "").strip()

    if request.GET.get("date") != "all":
        qs = qs.filter(visit_date=visit_date)
    if status:
        qs = qs.filter(status=status)
    if keyword:
        qs = qs.filter(Q(patient__name__icontains=keyword) | Q(reg_no__icontains=keyword))
    if request.GET.get("mine") and doctor:
        qs = qs.filter(doctor=doctor)

    qs = qs.order_by("-visit_date", "queue_no")
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page", 1))

    return render(request, "clinic/registration_list.html", {
        "page": page,
        "registrations": page.object_list,
        "visit_date": visit_date,
        "status": status,
        "name": keyword,
        "status_choices": Registration.STATUS_CHOICES,
        "can_create": has_any_permission(request.user, "接诊:写", "挂号:管理"),
    })


@login_required
def registration_create(request):
    """窗口挂号 / 医师加号。"""
    if not has_any_permission(request.user, "接诊:写", "挂号:管理"):
        return deny(request, "无挂号操作权限", perm_code="挂号:管理")

    doctor = _my_doctor(request.user)
    doctors = Doctor.objects.select_related("user").filter(is_active=True).order_by("department", "user__first_name")

    if request.method == "POST":
        patient = Patient.objects.filter(pk=_int(request.POST.get("patient_id")), is_deleted=False).first()
        doctor_obj = doctors.filter(pk=_int(request.POST.get("doctor_id"))).first()
        if not patient:
            messages.error(request, "请选择已建档的患者")
        elif not doctor_obj:
            messages.error(request, "请选择接诊医师")
        else:
            reg_type = request.POST.get("reg_type", "NORMAL")
            visit_date = _date(request.POST.get("visit_date"), timezone.localdate())
            reg = Registration(
                reg_no=Registration.generate_reg_no(),
                patient=patient,
                doctor=doctor_obj,
                visit_date=visit_date,
                time_slot=request.POST.get("time_slot") or TIME_SLOTS[0],
                reg_type=reg_type if reg_type in dict(Registration.TYPE_CHOICES) else "NORMAL",
                source="DOCTOR" if doctor else "WINDOW",
                fee=Registration.FEE_MAP.get(reg_type, 20),
                chief_complaint=(request.POST.get("chief_complaint") or "")[:200],
                created_by=request.user,
            )
            reg.queue_no = reg.next_queue_no()
            reg.save()
            _log(request, "CREATE", "registration", reg.id, f"挂号 {reg.reg_no}（{patient.name}）")
            messages.success(request, f"挂号成功：{reg.reg_no}，{doctor_obj} 第 {reg.queue_no} 号")
            return redirect("clinic:registration_list")

    recent_patients = Patient.objects.filter(is_deleted=False).order_by("-created_at")[:50]
    return render(request, "clinic/registration_form.html", {
        "doctors": doctors,
        "patients": recent_patients,
        "time_slots": TIME_SLOTS,
        "today": timezone.localdate(),
        "default_doctor": doctor,
    })


@login_required
@require_POST
def registration_call(request, pk):
    """叫号接诊：待就诊 → 就诊中，并跳转到病历书写。"""
    if not has_permission(request.user, "接诊:写"):
        return deny(request, "无接诊权限", perm_code="接诊:写")

    reg = get_object_or_404(Registration.objects.select_related("doctor"), pk=pk)
    doctor = _my_doctor(request.user)
    if doctor and reg.doctor_id != doctor.id:
        return deny(request, "只能接诊本人名下号源", perm_code="接诊:写")
    if reg.status not in ("WAITING", "IN_PROGRESS"):
        messages.warning(request, "该号源已完成或已取消，不能重复接诊")
        return redirect("clinic:workbench")

    if reg.status == "WAITING":
        reg.status = "IN_PROGRESS"
        reg.called_at = timezone.now()
        reg.save(update_fields=["status", "called_at"])
        _log(request, "CALL", "registration", reg.id, f"叫号接诊 {reg.reg_no}")

    record = MedicalRecord.objects.filter(registration=reg).first()
    if record:
        return redirect("clinic:record_edit", pk=record.pk)
    return redirect("clinic:record_create", reg_id=reg.pk)


@login_required
@require_POST
def registration_cancel(request, pk):
    """取消挂号（患者本人或窗口/医师）。"""
    reg = get_object_or_404(Registration.objects.select_related("patient"), pk=pk)
    patient = _my_patient(request.user)
    is_owner = patient and reg.patient_id == patient.id
    if not is_owner and not has_any_permission(request.user, "挂号:管理", "接诊:写"):
        return deny(request, "只能取消本人的挂号", perm_code="挂号:管理")
    if reg.status in ("FINISHED",):
        messages.error(request, "已完成的就诊不能取消")
    else:
        reg.status = "CANCELLED"
        reg.save(update_fields=["status"])
        _log(request, "CANCEL", "registration", reg.id, f"取消挂号 {reg.reg_no}")
        messages.success(request, "挂号已取消")
    return redirect(request.META.get("HTTP_REFERER") or "clinic:registration_list")


@login_required
@require_POST
def registration_pay(request, pk):
    """收费窗口确认挂号费。"""
    if not has_permission(request.user, "收费:确认"):
        return deny(request, "无收费权限", perm_code="收费:确认")
    reg = get_object_or_404(Registration, pk=pk)
    reg.pay_status = "PAID"
    reg.save(update_fields=["pay_status"])
    _log(request, "PAY", "registration", reg.id, f"挂号收费 {reg.reg_no} ￥{reg.fee}")
    messages.success(request, f"挂号费 ￥{reg.fee} 已登记")
    return redirect(request.META.get("HTTP_REFERER") or "clinic:registration_list")


# ============ 电子病历 ============

RECORD_FIELDS = [
    "chief_complaint", "present_illness", "past_history", "allergy_history", "family_history",
    "inspection", "auscultation", "inquiry", "palpation",
    "tcm_diagnosis", "syndrome", "western_diagnosis", "treatment", "advice",
]


def _fill_record(record, post):
    for name in RECORD_FIELDS:
        setattr(record, name, (post.get(name) or "").strip())
    record.temperature = post.get("temperature") or None
    record.pulse = _int(post.get("pulse"))
    record.respiration = _int(post.get("respiration"))
    record.blood_pressure = (post.get("blood_pressure") or "").strip()
    return record


@login_required
def record_create(request, reg_id):
    """接诊后书写电子病历。"""
    if not has_permission(request.user, "接诊:写"):
        return deny(request, "无病历书写权限", perm_code="接诊:写")

    doctor = _my_doctor(request.user)
    reg = get_object_or_404(Registration.objects.select_related("patient", "doctor"), pk=reg_id)
    if doctor and reg.doctor_id != doctor.id:
        return deny(request, "只能为本人接诊的号源书写病历", perm_code="接诊:写")

    existing = MedicalRecord.objects.filter(registration=reg).first()
    if existing:
        return redirect("clinic:record_edit", pk=existing.pk)

    record = MedicalRecord(
        patient=reg.patient,
        doctor=reg.doctor,
        registration=reg,
        chief_complaint=reg.chief_complaint,
        past_history=reg.patient.medical_history,
        allergy_history=reg.patient.allergy_history,
    )

    if request.method == "POST":
        if not (request.POST.get("chief_complaint") or "").strip():
            messages.error(request, "主诉为必填项")
        else:
            record = _fill_record(record, request.POST)
            record.record_no = MedicalRecord.generate_record_no()
            if request.POST.get("action") == "sign":
                record.status = "SIGNED"
                record.signed_at = timezone.now()
            record.save()
            _log(request, "CREATE", "medical_record", record.id, f"书写病历 {record.record_no}")
            if record.status == "SIGNED":
                _finish_registration(request, record)
                messages.success(request, "病历已签发，本次就诊完成")
            else:
                messages.success(request, "病历已保存为草稿")
            return redirect("clinic:record_detail", pk=record.pk)

    return render(request, "clinic/record_form.html", {
        "record": record, "reg": reg, "patient": reg.patient, "mode": "create",
    })


@login_required
def record_edit(request, pk):
    """修改病历草稿；已签发病历只能追加医嘱（质控留痕要求）。"""
    record = get_object_or_404(
        MedicalRecord.objects.select_related("patient", "doctor", "registration"), pk=pk
    )
    doctor = _my_doctor(request.user)
    is_owner = doctor and record.doctor_id == doctor.id
    if not is_owner and not _can_view_all(request.user):
        return deny(request, "只能编辑本人书写的病历", perm_code="接诊:写")

    if request.method == "POST":
        if record.status == "SIGNED":
            messages.error(request, "已签发病历不可修改，如需补充请联系医务科")
        elif not (request.POST.get("chief_complaint") or "").strip():
            messages.error(request, "主诉为必填项")
        else:
            _fill_record(record, request.POST)
            if request.POST.get("action") == "sign":
                record.status = "SIGNED"
                record.signed_at = timezone.now()
            record.save()
            _log(request, "UPDATE", "medical_record", record.id, f"修改病历 {record.record_no}")
            if record.status == "SIGNED":
                _finish_registration(request, record)
                messages.success(request, "病历已签发")
            else:
                messages.success(request, "病历草稿已更新")
            return redirect("clinic:record_detail", pk=record.pk)

    return render(request, "clinic/record_form.html", {
        "record": record, "reg": record.registration, "patient": record.patient, "mode": "edit",
    })


def _finish_registration(request, record):
    """病历签发后关闭号源并刷新患者最近就诊信息。"""
    reg = record.registration
    if reg:
        reg.status = "FINISHED"
        reg.finished_at = timezone.now()
        reg.save(update_fields=["status", "finished_at"])
    patient = record.patient
    patient.last_visit_at = timezone.now()
    summary = f"{record.chief_complaint}；中医诊断：{record.tcm_diagnosis or '待补充'}"
    patient.visit_summary = summary[:200]
    patient.save(update_fields=["last_visit_at", "visit_summary"])


@login_required
def record_detail(request, pk):
    """病历详情，按角色做数据隔离。"""
    record = get_object_or_404(
        MedicalRecord.objects.select_related("patient", "doctor__user", "registration", "prescription"), pk=pk
    )
    patient = _my_patient(request.user)
    doctor = _my_doctor(request.user)
    is_owner_patient = patient and record.patient_id == patient.id
    is_owner_doctor = doctor and record.doctor_id == doctor.id
    can_read = has_permission(request.user, "病历:读")

    if not (is_owner_patient or is_owner_doctor or can_read):
        return deny(request, "无权查看该病历", perm_code="病历:读")

    return render(request, "clinic/record_detail.html", {
        "record": record,
        "patient": record.patient,
        "prescription": record.prescription,
        "can_edit": is_owner_doctor and record.status == "DRAFT",
        "can_prescribe": is_owner_doctor and has_permission(request.user, "处方:新增"),
        "is_patient_view": bool(is_owner_patient and not is_owner_doctor and not can_read),
    })


@login_required
def my_visits(request):
    """患者自助：我的就诊记录与病历。"""
    patient = _my_patient(request.user)
    if not patient:
        messages.warning(request, "当前账号尚未绑定患者档案，请联系门诊窗口建档")
        return redirect("users:profile")

    records = (
        MedicalRecord.objects.filter(patient=patient)
        .select_related("doctor__user", "prescription")
        .order_by("-created_at")
    )
    page = Paginator(records, 10).get_page(request.GET.get("page", 1))
    registrations = (
        Registration.objects.filter(patient=patient)
        .select_related("doctor__user")
        .order_by("-visit_date", "-queue_no")[:10]
    )
    return render(request, "clinic/my_visits.html", {
        "patient": patient,
        "page": page,
        "records": page.object_list,
        "registrations": registrations,
        "requests": patient.record_requests.all()[:5],
    })


@login_required
def appointment_create(request):
    """患者在线预约挂号。"""
    patient = _my_patient(request.user)
    if not patient:
        messages.warning(request, "请先完成患者建档后再预约")
        return redirect("users:profile")

    doctors = Doctor.objects.select_related("user").filter(is_active=True).order_by("department", "user__first_name")
    days = [timezone.localdate() + timedelta(days=i) for i in range(14)]

    if request.method == "POST":
        doctor_obj = doctors.filter(pk=_int(request.POST.get("doctor_id"))).first()
        visit_date = _date(request.POST.get("visit_date"), timezone.localdate())
        reg_type = request.POST.get("reg_type", "NORMAL")
        if not doctor_obj:
            messages.error(request, "请选择接诊医师")
        elif visit_date < timezone.localdate():
            messages.error(request, "预约日期不能早于今天")
        elif Registration.objects.filter(
            patient=patient, doctor=doctor_obj, visit_date=visit_date, status__in=("WAITING", "IN_PROGRESS")
        ).exists():
            messages.error(request, "您当天已预约该医师，请勿重复挂号")
        else:
            reg = Registration(
                reg_no=Registration.generate_reg_no(),
                patient=patient,
                doctor=doctor_obj,
                visit_date=visit_date,
                time_slot=request.POST.get("time_slot") or TIME_SLOTS[0],
                reg_type=reg_type if reg_type in dict(Registration.TYPE_CHOICES) else "NORMAL",
                source="ONLINE",
                fee=Registration.FEE_MAP.get(reg_type, 20),
                chief_complaint=(request.POST.get("chief_complaint") or "")[:200],
                created_by=request.user,
            )
            reg.queue_no = reg.next_queue_no()
            reg.save()
            _log(request, "CREATE", "registration", reg.id, f"线上预约 {reg.reg_no}")
            messages.success(
                request, f"预约成功：{visit_date} {reg.time_slot}，{doctor_obj}，排队号 {reg.queue_no}"
            )
            return redirect("clinic:my_visits")

    return render(request, "clinic/appointment_form.html", {
        "doctors": doctors, "days": days, "time_slots": TIME_SLOTS, "patient": patient,
        "today": timezone.localdate(),
    })


# ============ 病案服务申请 ============

@login_required
def request_list(request):
    """病案申请列表：患者看本人，受理岗位看全量。"""
    can_handle = has_permission(request.user, "病历申请:处理")
    patient = _my_patient(request.user)
    if not can_handle and not patient:
        return deny(request, "仅患者本人或病案受理岗位可查看", perm_code="病历申请:处理")

    qs = MedicalRecordRequest.objects.select_related("patient", "handler")
    if not can_handle:
        qs = qs.filter(patient=patient)

    status = request.GET.get("status", "").strip()
    if status:
        qs = qs.filter(status=status)
    if request.GET.get("type"):
        qs = qs.filter(request_type=request.GET["type"])

    page = Paginator(qs.order_by("-created_at"), PAGE_SIZE).get_page(request.GET.get("page", 1))
    counts = {
        key: qs.filter(status=key).count() for key, _ in MedicalRecordRequest.STATUS_CHOICES
    }
    return render(request, "clinic/request_list.html", {
        "page": page,
        "requests": page.object_list,
        "can_handle": can_handle,
        "status": status,
        "status_choices": MedicalRecordRequest.STATUS_CHOICES,
        "type_choices": MedicalRecordRequest.TYPE_CHOICES,
        "counts": counts,
    })


@login_required
def request_create(request):
    """患者发起病案服务申请。"""
    patient = _my_patient(request.user)
    if not patient:
        messages.warning(request, "请先完成患者建档")
        return redirect("users:profile")

    if request.method == "POST":
        purpose = (request.POST.get("purpose") or "").strip()
        phone = (request.POST.get("phone") or "").strip()
        delivery = request.POST.get("delivery", "PICKUP")
        if not purpose:
            messages.error(request, "请填写申请用途")
        elif not phone:
            messages.error(request, "请填写联系电话")
        elif delivery == "MAIL" and not (request.POST.get("address") or "").strip():
            messages.error(request, "邮寄方式需填写收件地址")
        else:
            req = MedicalRecordRequest(
                request_no=MedicalRecordRequest.generate_request_no(),
                patient=patient,
                applicant_name=(request.POST.get("applicant_name") or request.user.display_name)[:64],
                relation=request.POST.get("relation", "SELF"),
                phone=phone[:20],
                id_card_last4=(request.POST.get("id_card_last4") or "")[:8],
                request_type=request.POST.get("request_type", "COPY"),
                purpose=purpose[:200],
                copies=max(1, min(_int(request.POST.get("copies"), 1), 20)),
                delivery=delivery if delivery in dict(MedicalRecordRequest.DELIVERY_CHOICES) else "PICKUP",
                address=(request.POST.get("address") or "")[:200],
            )
            req.save()
            _log(request, "CREATE", "record_request", req.id, f"提交病案申请 {req.request_no}")
            messages.success(request, f"申请已提交，单号 {req.request_no}，请等待病案室受理")
            return redirect("clinic:request_detail", pk=req.pk)

    return render(request, "clinic/request_form.html", {
        "patient": patient,
        "type_choices": MedicalRecordRequest.TYPE_CHOICES,
        "relation_choices": MedicalRecordRequest.RELATION_CHOICES,
        "delivery_choices": MedicalRecordRequest.DELIVERY_CHOICES,
    })


@login_required
def request_detail(request, pk):
    req = get_object_or_404(MedicalRecordRequest.objects.select_related("patient", "handler"), pk=pk)
    patient = _my_patient(request.user)
    is_owner = patient and req.patient_id == patient.id
    can_handle = has_permission(request.user, "病历申请:处理")
    if not (is_owner or can_handle):
        return deny(request, "无权查看该申请", perm_code="病历申请:处理")
    return render(request, "clinic/request_detail.html", {
        "req": req, "can_handle": can_handle, "is_owner": bool(is_owner),
    })


@login_required
@require_POST
def request_handle(request, pk):
    """病案室受理 / 完成 / 驳回。"""
    if not has_permission(request.user, "病历申请:处理"):
        return deny(request, "无病案受理权限", perm_code="病历申请:处理")

    req = get_object_or_404(MedicalRecordRequest, pk=pk)
    action = request.POST.get("action")
    note = (request.POST.get("handle_note") or "").strip()[:300]
    now = timezone.now()

    if action == "accept" and req.status == "PENDING":
        req.status = "ACCEPTED"
        req.accepted_at = now
        req.handler = request.user
        req.handle_note = note
        messages.success(request, "已受理，请按申请类型准备材料")
    elif action == "complete" and req.status in ("PENDING", "ACCEPTED"):
        req.status = "COMPLETED"
        req.completed_at = now
        req.accepted_at = req.accepted_at or now
        req.handler = request.user
        req.handle_note = note
        req.pickup_code = "".join(random.choices(string.digits, k=6))
        messages.success(request, f"已完成，取件码 {req.pickup_code}")
    elif action == "reject":
        req.status = "REJECTED"
        req.handler = request.user
        req.handle_note = note
        messages.warning(request, "已驳回该申请")
    else:
        messages.error(request, "当前状态不支持该操作")
        return redirect("clinic:request_detail", pk=req.pk)

    req.save()
    _log(request, action.upper(), "record_request", req.id, f"病案申请 {req.request_no} -> {req.status}")
    return redirect("clinic:request_detail", pk=req.pk)


# ============ 工作台统计（供驾驶舱复用） ============

def clinic_snapshot():
    """今日门诊概览，供管理驾驶舱展示。"""
    today = timezone.localdate()
    regs = Registration.objects.filter(visit_date=today)
    return {
        "today_total": regs.count(),
        "today_finished": regs.filter(status="FINISHED").count(),
        "today_waiting": regs.filter(status="WAITING").count(),
        "pending_requests": MedicalRecordRequest.objects.filter(status="PENDING").count(),
        "signed_records": MedicalRecord.objects.filter(
            status="SIGNED", signed_at__date=today
        ).count(),
    }
