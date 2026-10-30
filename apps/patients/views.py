"""患者信息管理视图。"""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from apps.users.permissions import deny, get_client_ip, has_permission, parse_json_body
from apps.utils.crypto import decrypt, encrypt, hash_value, mask_id_card, mask_phone

from .models import Patient

PAGE_SIZE = 20


def _safe_int(raw, default=None):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@login_required
def patient_list(request):
    """患者列表：分页 + 关键字 + 条件筛选。"""
    if not has_permission(request.user, "患者:读"):
        return deny(request, perm_code="患者:读")

    qs = Patient.objects.filter(is_deleted=False)

    name = request.GET.get("name", "").strip()
    patient_no = request.GET.get("patient_no", "").strip()
    gender = request.GET.get("gender", "").strip()
    age_min = _safe_int(request.GET.get("age_min"))
    age_max = _safe_int(request.GET.get("age_max"))

    if name:
        qs = qs.filter(name__icontains=name)
    if patient_no:
        qs = qs.filter(patient_no__icontains=patient_no)
    if gender:
        qs = qs.filter(gender=gender)
    if age_min is not None:
        qs = qs.filter(age__gte=age_min)
    if age_max is not None:
        qs = qs.filter(age__lte=age_max)

    qs = qs.order_by("-last_visit_at", "-created_at")
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page", 1))

    # 列表页掩码展示
    for p in page.object_list:
        p.masked_id_card = mask_id_card(decrypt(p.id_card_encrypted)) if p.id_card_encrypted else ""
        p.masked_phone = mask_phone(decrypt(p.phone_encrypted)) if p.phone_encrypted else ""

    return render(request, "patients/patient_list.html", {
        "page": page,
        "patients": page.object_list,
        # 只有真正能开方的账号（医师档案 + 处方:新增）才显示"开方"入口
        "can_create_presc": bool(getattr(request.user, "doctor", None))
        and has_permission(request.user, "处方:新增"),
    })


@login_required
def patient_detail(request, pk):
    """患者详情：按权限解密敏感字段。"""
    patient = get_object_or_404(Patient, pk=pk, is_deleted=False)

    is_owner = getattr(request.user, "patient_profile", None) == patient
    can_view_sensitive = has_permission(request.user, "患者:读")
    if not can_view_sensitive and not is_owner:
        return deny(request, "只能查看本人的患者档案", perm_code="患者:读")

    if can_view_sensitive:
        patient.id_card = decrypt(patient.id_card_encrypted) if patient.id_card_encrypted else ""
        patient.phone = decrypt(patient.phone_encrypted) if patient.phone_encrypted else ""
    else:
        patient.id_card = mask_id_card(decrypt(patient.id_card_encrypted)) if patient.id_card_encrypted else ""
        patient.phone = mask_phone(decrypt(patient.phone_encrypted)) if patient.phone_encrypted else ""

    return render(request, "patients/patient_detail.html", {
        "patient": patient, "can_view_sensitive": can_view_sensitive,
    })


@require_GET
def api_patient_list(request):
    """GET /api/v1/patients 患者分页与条件检索。"""
    if not request.user.is_authenticated:
        return JsonResponse({"code": 401, "message": "未登录"}, status=401)
    if not has_permission(request.user, "患者:读"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    qs = Patient.objects.filter(is_deleted=False)
    name = request.GET.get("name", "")
    if name:
        qs = qs.filter(name__icontains=name)

    page_no = _safe_int(request.GET.get("page"), 1) or 1
    page_size = min(max(_safe_int(request.GET.get("page_size"), PAGE_SIZE) or PAGE_SIZE, 1), 100)
    page = Paginator(qs.order_by("-created_at"), page_size).get_page(page_no)

    items = [
        {
            "id": p.id,
            "patient_no": p.patient_no,
            "name": p.name,
            "gender": p.get_gender_display(),
            "age": p.age,
            "id_card": mask_id_card(decrypt(p.id_card_encrypted)) if p.id_card_encrypted else "",
            "phone": mask_phone(decrypt(p.phone_encrypted)) if p.phone_encrypted else "",
        }
        for p in page.object_list
    ]
    return JsonResponse({
        "code": 200, "message": "ok",
        "data": {"items": items, "total": page.paginator.count, "page": page_no, "page_size": page_size},
    })


@require_POST
def api_patient_create(request):
    """POST /api/v1/patients 患者建档。"""
    if not request.user.is_authenticated:
        return JsonResponse({"code": 401, "message": "未登录"}, status=401)
    if not has_permission(request.user, "患者:新增"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    data, error = parse_json_body(request)
    if error:
        return error

    name = (data.get("name") or "").strip()
    if not name:
        return JsonResponse({"code": 400, "message": "姓名必填"}, status=400)

    age = _safe_int(data.get("age"))
    if age is not None and (age < 0 or age > 150):
        return JsonResponse({"code": 400, "message": "年龄应在 0~150 之间"}, status=400)

    id_card = (data.get("id_card") or "").strip()
    phone = (data.get("phone") or "").strip()

    # 身份证查重（哈希列等值查询，不解密全表）
    if id_card:
        id_hash = hash_value(id_card)
        if Patient.objects.filter(id_card_hash=id_hash, is_deleted=False).exists():
            return JsonResponse({"code": 409, "message": "该身份证号已建档"}, status=409)

    patient = Patient(
        patient_no=Patient.generate_patient_no(),
        name=name,
        gender=data.get("gender", "U"),
        age=age,
        id_card_encrypted=encrypt(id_card),
        id_card_hash=hash_value(id_card),
        phone_encrypted=encrypt(phone),
        phone_hash=hash_value(phone),
        allergy_history=data.get("allergy_history", ""),
        medical_history=data.get("medical_history", ""),
        constitution=data.get("constitution", "BALANCED"),
        pregnancy_status=data.get("pregnancy_status", "NONE"),
        visit_summary=data.get("visit_summary", ""),
    )
    patient.save()

    from apps.stats.models import OperationLog

    OperationLog.objects.create(
        operator=request.user, action_type="CREATE", object_type="patient",
        object_id=str(patient.id), detail=f"建档患者 {patient.name}",
        ip=get_client_ip(request), result="success",
    )
    return JsonResponse({
        "code": 200, "message": "ok",
        "data": {"id": patient.id, "patient_no": patient.patient_no},
    })
