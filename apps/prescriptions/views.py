"""处方开具管理视图。

医师创建处方、维护药味明细、提交预审；
药房发药、收费登记、患者查看本人处方。
"""
import io
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.patients.models import Patient
from apps.prescriptions import selectors
from apps.prescriptions.models import FormulaTemplate, Herb, Prescription, PrescriptionItem
from apps.reviews import engine, services
from apps.reviews.services import BusinessError
from apps.users.permissions import deny, get_client_ip, has_any_permission, has_permission, parse_json_body

logger = logging.getLogger(__name__)

PAGE_SIZE = 20


def _log(request, action_type, object_type, object_id, detail, result="success"):
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


# ============ 页面视图 ============

@login_required
def prescription_list(request):
    """处方列表。

    医师默认只看自己开具的处方；管理员/医务科等具备全量查看权的岗位
    看 ``visible_prescriptions`` 给出的全部范围（权限收口在 selectors 内）。
    """
    doctor = getattr(request.user, "doctor", None)
    qs = selectors.visible_prescriptions(request.user)

    if doctor:
        qs = qs.filter(doctor=doctor)
    elif not has_any_permission(request.user, "统计:读", "医务科:统计", "审核:读"):
        # 既不是医师、也没有任何全量查看权，才拒绝
        return deny(request, "仅医师或具备全量查看权限的岗位可访问处方列表", perm_code="处方:读")

    qs = qs.select_related("patient", "doctor__user")
    status = request.GET.get("status", "").strip()
    keyword = request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status=status)
    if keyword:
        qs = qs.filter(Q(presc_no__icontains=keyword) | Q(patient__name__icontains=keyword))

    page = Paginator(qs.order_by("-created_at"), PAGE_SIZE).get_page(request.GET.get("page", 1))
    return render(request, "prescriptions/prescription_list.html", {
        "page": page,
        "prescriptions": page.object_list,
        "status": status,
        "q": keyword,
        "status_choices": Prescription.STATUS_CHOICES,
        "is_doctor_view": bool(doctor),
    })


@login_required
def my_prescriptions(request):
    """患者自助查询本人处方（数据按 patient.user 隔离）。"""
    if not has_permission(request.user, "我的处方:读") and not has_permission(request.user, "患者:读"):
        return deny(request, "仅可查询本人处方", perm_code="我的处方:读")

    qs = selectors.visible_prescriptions(request.user).select_related("patient", "doctor__user")
    page = Paginator(qs.order_by("-created_at"), PAGE_SIZE).get_page(request.GET.get("page", 1))
    has_profile = hasattr(request.user, "patient_profile")
    return render(request, "prescriptions/my_prescriptions.html", {
        "page": page,
        "prescriptions": page.object_list,
        "has_profile": has_profile,
    })


@login_required
def prescription_create(request):
    """创建处方草稿页面。"""
    doctor = getattr(request.user, "doctor", None)
    if not doctor:
        return deny(request, "仅医师账号可开具处方", perm_code="处方:新增")
    patients = Patient.objects.filter(is_deleted=False).order_by("-last_visit_at", "-created_at")[:300]
    templates = FormulaTemplate.objects.prefetch_related("items__herb").all()
    herbs = Herb.objects.all().order_by("pinyin_initial", "name")
    return render(request, "prescriptions/prescription_form.html", {
        "patients": patients, "templates": templates, "herbs": herbs,
    })


@login_required
def prescription_detail(request, pk):
    """处方详情（按角色做数据隔离）。"""
    prescription = selectors.get_prescription_for(request.user, pk)
    items = prescription.items.select_related("herb").all()
    return render(request, "prescriptions/prescription_detail.html", {
        "prescription": prescription, "items": items,
    })


@login_required
def payment_queue(request):
    """缴费登记窗口：已审核通过、待缴费的处方。"""
    if not has_permission(request.user, "收费:确认"):
        return deny(request, perm_code="收费:确认")
    page = Paginator(selectors.unpaid_queue(), PAGE_SIZE).get_page(request.GET.get("page", 1))
    return render(request, "prescriptions/payment_queue.html", {"page": page, "prescriptions": page.object_list})


@login_required
def dispense_queue(request):
    """发药窗口：已缴费、待发药的处方。"""
    if not has_permission(request.user, "发药:确认"):
        return deny(request, perm_code="发药:确认")
    page = Paginator(selectors.dispense_queue(), PAGE_SIZE).get_page(request.GET.get("page", 1))
    return render(request, "prescriptions/dispense_queue.html", {
        "page": page,
        "prescriptions": page.object_list,
        "recent": Prescription.objects.filter(status="DISPENSED", is_deleted=False)
        .select_related("patient")
        .order_by("-updated_at")[:8],
    })


@login_required
def herb_list(request):
    """药材字典管理页（拼音首字母检索、产地/炮制/库存展示）。

    药房管理员需要查药材与库存，因此「字典:维护」与「药房:管理」都放行；
    真正的字典编辑（导入、改库存）仍由各自视图单独把关。
    """
    if not has_any_permission(request.user, "字典:维护", "药房:管理"):
        return deny(request, "仅药房或字典维护人员可查看药材目录", perm_code="字典:维护")
    can_edit_dict = has_permission(request.user, "字典:维护")
    can_adjust_stock = has_permission(request.user, "药房:管理")
    q = request.GET.get("q", "").strip()
    herbs = Herb.objects.all().order_by("pinyin_initial", "name")
    if q:
        herbs = herbs.filter(
            Q(name__icontains=q)
            | Q(pinyin_initial__icontains=q.upper())
            | Q(origin__icontains=q)
            | Q(processing__icontains=q)
            | Q(efficacy__icontains=q)
        )
    page = Paginator(herbs, 30).get_page(request.GET.get("page", 1))
    return render(request, "prescriptions/herb_list.html", {
        "page": page,
        "herbs": page.object_list,
        "q": q,
        "can_edit_dict": can_edit_dict,
        "can_adjust_stock": can_adjust_stock,
    })


@login_required
def herb_card(request, pk):
    """药材信息卡片（打印/PDF 导出）。"""
    herb = get_object_or_404(Herb, pk=pk)
    batches = herb.batches.select_related("supplier").order_by("-inbound_at")
    return render(request, "prescriptions/herb_card.html", {"herb": herb, "batches": batches})


@login_required
def herb_card_pdf(request, pk):
    """药材信息卡片 PDF 导出。"""
    herb = get_object_or_404(Herb, pk=pk)
    batches = herb.batches.select_related("supplier").order_by("-inbound_at")
    html = render(request, "prescriptions/herb_card.html", {"herb": herb, "batches": batches}).content.decode("utf-8")
    return _html_to_pdf_response(html, f"药材卡片_{herb.name}.pdf")


@login_required
def herb_import(request):
    """药材字典 Excel 批量导入页面。"""
    if not has_permission(request.user, "字典:维护"):
        return deny(request, perm_code="字典:维护")
    if request.method == "POST":
        f = request.FILES.get("file")
        if not f:
            messages.error(request, "请选择要上传的 Excel 文件")
            return redirect("prescriptions:herb_list")
        result = _import_herbs_excel(f)
        if result["errors"]:
            messages.warning(request, f"导入完成：成功 {result['success']} 条，跳过 {result['skip']} 条；" + "；".join(result["errors"][:3]))
        else:
            messages.success(request, f"导入完成：成功 {result['success']} 条，跳过 {result['skip']} 条")
        return redirect("prescriptions:herb_list")
    return render(request, "prescriptions/herb_import.html")


@login_required
@require_POST
def herb_stock_adjust(request, pk):
    """库存调整（入库/盘点），兑现「药房:管理」权限。"""
    if not has_permission(request.user, "药房:管理"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    data, error = parse_json_body(request)
    if error:
        return error
    try:
        delta = float(data.get("delta_kg", 0))
    except (TypeError, ValueError):
        return JsonResponse({"code": 400, "message": "调整数量必须是数字"}, status=400)
    if delta == 0:
        return JsonResponse({"code": 400, "message": "调整数量不能为 0"}, status=400)

    herb = get_object_or_404(Herb, pk=pk)
    from django.db.models import F

    Herb.objects.filter(pk=herb.pk).update(stock=F("stock") + delta)
    herb.refresh_from_db()
    _log(request, "STOCK_ADJUST", "herb", herb.id, f"{herb.name} 库存调整 {delta:+g}kg → {herb.stock}kg")
    return JsonResponse({"code": 200, "message": "库存已更新", "data": {"stock": float(herb.stock)}})


@login_required
def prescription_print(request, pk):
    """处方单 A4 打印视图（含处方编号、医师/药师签章位置）。"""
    prescription = selectors.get_prescription_for(request.user, pk)
    items = prescription.items.select_related("herb").all()
    reviews = prescription.review_records.select_related("pharmacist__user").order_by("created_at")
    return render(request, "prescriptions/prescription_print.html", {
        "prescription": prescription, "items": items, "reviews": reviews,
    })


@login_required
def prescription_pdf(request, pk):
    """处方单 PDF 导出（复用打印样式，生成 PDF 供下载）。"""
    prescription = selectors.get_prescription_for(request.user, pk)
    items = prescription.items.select_related("herb").all()
    reviews = prescription.review_records.select_related("pharmacist__user").order_by("created_at")
    html = render(request, "prescriptions/prescription_print.html", {
        "prescription": prescription, "items": items, "reviews": reviews,
    }).content.decode("utf-8")
    return _html_to_pdf_response(html, f"处方单_{prescription.presc_no}.pdf")


# ============ API 接口 ============

@require_POST
def api_prescription_create(request):
    """POST /api/v1/prescriptions 新建处方（草稿）。"""
    if not request.user.is_authenticated:
        return JsonResponse({"code": 401, "message": "未登录"}, status=401)
    if not has_permission(request.user, "处方:新增"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    doctor = getattr(request.user, "doctor", None)
    if not doctor:
        return JsonResponse({"code": 403, "message": "当前用户非医师"}, status=403)

    data, error = parse_json_body(request)
    if error:
        return error

    patient_id = data.get("patient_id")
    if not patient_id:
        return JsonResponse({"code": 400, "message": "请选择患者"}, status=400)

    patient = Patient.objects.filter(pk=patient_id, is_deleted=False).first()
    if not patient:
        return JsonResponse({"code": 404, "message": "患者不存在"}, status=404)

    pickup_time = parse_datetime(data.get("pickup_time") or "") if data.get("pickup_time") else None
    presc_type = data.get("presc_type", "OUTPATIENT")
    if presc_type not in dict(Prescription.TYPE_CHOICES):
        return JsonResponse({"code": 400, "message": "处方类型非法"}, status=400)

    try:
        total_doses = int(data.get("total_doses") or 1)
    except (TypeError, ValueError):
        return JsonResponse({"code": 400, "message": "剂数必须是整数"}, status=400)
    if total_doses < 1 or total_doses > 60:
        return JsonResponse({"code": 400, "message": "剂数应在 1~60 之间"}, status=400)

    prescription = Prescription.create_with_generated_no(
        patient=patient,
        doctor=doctor,
        diagnosis=data.get("diagnosis", "")[:200],
        syndrome=data.get("syndrome", "")[:200],
        presc_type=presc_type,
        decoction_note=data.get("decoction_note", "")[:200],
        pickup_time=pickup_time,
        pickup_person=data.get("pickup_person", "")[:64],
        total_doses=total_doses,
        status="DRAFT",
    )
    _log(request, "CREATE", "prescription", prescription.id, f"创建处方 {prescription.presc_no}")
    return JsonResponse({
        "code": 200, "message": "ok",
        "data": {"id": prescription.id, "presc_no": prescription.presc_no},
    })


@require_http_methods(["PUT", "PATCH", "POST"])
def api_prescription_items(request, pk):
    """PUT /api/v1/prescriptions/{id} 修改草稿明细。"""
    if not request.user.is_authenticated:
        return JsonResponse({"code": 401, "message": "未登录"}, status=401)
    if not has_permission(request.user, "处方:修改"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    prescription = selectors.get_prescription_for(request.user, pk)
    if prescription.status != "DRAFT":
        return JsonResponse({"code": 409, "message": "仅草稿状态可修改明细"}, status=409)

    data, error = parse_json_body(request)
    if error:
        return error

    items = data.get("items", [])
    if not isinstance(items, list) or not items:
        return JsonResponse({"code": 400, "message": "处方至少需要一味药材"}, status=400)

    herb_ids = [it.get("herb_id") for it in items if isinstance(it, dict)]
    herb_map = {h.id: h for h in Herb.objects.filter(id__in=herb_ids)}
    if len(herb_map) != len(set(herb_ids)):
        return JsonResponse({"code": 400, "message": "存在无效的药材"}, status=400)

    rows = []
    for it in items:
        try:
            dose = float(it.get("dose") or 0)
        except (TypeError, ValueError):
            return JsonResponse({"code": 400, "message": "剂量必须是数字"}, status=400)
        if dose <= 0 or dose > 500:
            return JsonResponse({"code": 400, "message": f"剂量 {dose}g 不合理，应在 0~500g 之间"}, status=400)
        herb = herb_map[int(it["herb_id"])]
        rows.append((herb, dose, (it.get("decoction") or "")[:100], (it.get("usage") or "")[:100]))

    with transaction.atomic():
        prescription.items.all().delete()
        PrescriptionItem.objects.bulk_create([
            PrescriptionItem(prescription=prescription, herb=herb, dose=dose, decoction=decoction, usage=usage)
            for herb, dose, decoction, usage in rows
        ])
        # 明细变了，旧快照失效
        Prescription.objects.filter(pk=prescription.pk).update(precheck_snapshot="", updated_at=django_now())

    _log(request, "UPDATE", "prescription", pk, f"修改处方明细 {prescription.presc_no}")
    return JsonResponse({"code": 200, "message": "明细已保存", "data": {"id": pk}})


@require_POST
def api_prescription_submit(request, pk):
    """POST /api/v1/prescriptions/{id}/submit 提交待预审。"""
    if not request.user.is_authenticated:
        return JsonResponse({"code": 401, "message": "未登录"}, status=401)
    if not has_permission(request.user, "处方:提交"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    prescription = selectors.get_prescription_for(request.user, pk)
    items = list(prescription.items.select_related("herb"))
    if not items:
        return JsonResponse({"code": 422, "message": "处方明细为空，无法提交"}, status=422)

    # 剂量上限校验（模型层业务规则，先行拦截，避免把明显错误送到药师手里）
    for item in items:
        if item.herb.max_dose and float(item.dose) > float(item.herb.max_dose):
            return JsonResponse({
                "code": 422,
                "message": f"药材 {item.herb.name} 剂量 {item.dose}g 超过上限 {item.herb.max_dose}g",
            }, status=422)

    with transaction.atomic():
        from apps.reviews import state_machine

        ok, target = state_machine.transition(prescription, "submit")
        if not ok:
            return JsonResponse({"code": 409, "message": target}, status=409)

        precheck = engine.precheck_prescription(prescription, items=items)
        engine.save_precheck_snapshot(prescription, precheck)
        event = "precheck_block" if precheck["blocked"] else "precheck_pass"
        ok, _ = state_machine.transition(prescription, event)
        if not ok:
            return JsonResponse({"code": 409, "message": "预审状态流转失败，请刷新重试"}, status=409)

    _log(request, "SUBMIT", "prescription", pk, f"提交处方 {prescription.presc_no}")
    services.notify_reviewers(prescription, blocked=precheck["blocked"])

    return JsonResponse({
        "code": 200,
        "message": "已提交预审",
        "data": {"status": prescription.status, "precheck": precheck},
    })


@require_POST
def api_prescription_pay(request, pk):
    """POST /api/v1/prescriptions/{id}/pay 缴费登记。"""
    if not has_permission(request.user, "收费:确认"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)

    data, error = parse_json_body(request)
    if error:
        return error

    prescription = selectors.get_prescription_for(request.user, pk)
    try:
        services.pay(prescription, request.user, amount=data.get("amount"))
    except BusinessError as exc:
        return JsonResponse({"code": exc.status, "message": exc.message}, status=exc.status)

    _log(request, "PAY", "prescription", pk, f"缴费登记 {prescription.presc_no}")
    return JsonResponse({"code": 200, "message": "缴费成功", "data": {"pay_status": "PAID"}})


@require_GET
def api_formula_detail(request, formula_id):
    """GET /api/v1/formulas/{id} 方剂模板详情（一键引用）。"""
    if not request.user.is_authenticated:
        return JsonResponse({"code": 401, "message": "未登录"}, status=401)
    formula = get_object_or_404(FormulaTemplate, pk=formula_id)
    items = list(formula.items.select_related("herb").values("herb_id", "herb__name", "dose", "unit"))
    return JsonResponse({"code": 200, "message": "ok", "data": {"name": formula.name, "items": items}})


# ============ 内部工具 ============

def django_now():
    from django.utils import timezone

    return timezone.now()


def _html_to_pdf_response(html, filename):
    """将 HTML 转成 PDF 响应。

    缺少 WeasyPrint 时降级返回可打印 HTML（浏览器 Ctrl+P 可另存 PDF），
    并记录告警——但绝不静默吞掉错误。
    """
    try:
        from weasyprint import HTML

        pdf = HTML(string=html, base_url=None).write_pdf()
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except ImportError:
        logger.warning("未安装 weasyprint，PDF 导出降级为可打印 HTML（pip install weasyprint 可启用）")
    except Exception:
        logger.exception("PDF 生成失败，降级返回可打印 HTML：%s", filename)

    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["X-PDF-Fallback"] = "1"
    return response


def _import_herbs_excel(fileobj):
    """解析 Excel 批量导入药材。返回 {"success", "skip", "errors"}。"""
    import openpyxl

    success = 0
    skip = 0
    errors = []

    try:
        wb = openpyxl.load_workbook(io.BytesIO(fileobj.read()), data_only=True)
    except Exception as exc:
        logger.warning("Excel 解析失败：%s", exc)
        return {"success": 0, "skip": 0, "errors": ["无法读取该文件，请确认是 xlsx 格式"]}

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {"success": 0, "skip": 0, "errors": ["工作表为空"]}

    header = [str(c).strip() if c else "" for c in rows[0]]
    if "药材名称" not in header and (not header or header[0] != "药材名称"):
        return {"success": 0, "skip": 0, "errors": ["首列应为「药材名称」，请使用模板表头"]}

    field_map = {
        "拼音首字母": "pinyin_initial", "类别": "category", "性味归经": "property",
        "功效主治": "efficacy", "道地产地": "origin", "炮制规格": "processing", "单位": "unit",
    }
    numeric_map = {"剂量上限": "max_dose", "常用剂量": "default_dose", "库存": "stock", "预警线": "stock_warning"}

    for line_no, row in enumerate(rows[1:], start=2):
        if not row or not row[0]:
            continue
        name = str(row[0]).strip()
        if not name:
            skip += 1
            continue

        defaults = {}
        for idx, col in enumerate(header):
            if idx >= len(row) or row[idx] is None:
                continue
            if col in field_map:
                defaults[field_map[col]] = str(row[idx]).strip()
            elif col in numeric_map:
                try:
                    defaults[numeric_map[col]] = float(row[idx])
                except (ValueError, TypeError):
                    errors.append(f"第 {line_no} 行「{col}」不是数字，已忽略该字段")
            elif col == "毒性":
                defaults["is_toxic"] = str(row[idx]).strip() in ("是", "1", "true", "True", "毒")
            elif col == "拼音":
                defaults["pinyin"] = str(row[idx]).strip()

        if not defaults.get("pinyin_initial") and defaults.get("pinyin"):
            defaults["pinyin_initial"] = "".join(w[0] for w in defaults["pinyin"].split()).upper()

        try:
            _, created = Herb.objects.get_or_create(name=name, defaults=defaults)
        except Exception:
            logger.exception("导入第 %s 行失败", line_no)
            errors.append(f"第 {line_no} 行「{name}」写入失败")
            continue

        if created:
            success += 1
        else:
            skip += 1

    return {"success": success, "skip": skip, "errors": errors}
