"""用户登录、注册、退出与医师管理视图。"""
import json
import logging
import re
from datetime import date

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.users import captcha, services
from apps.users.models import (
    APPLY_ROLE_CHOICES,
    APPLY_STATUS_CHOICES,
    Doctor,
    Permission,
    Pharmacist,
    Role,
    RolePermission,
    User,
    UserRole,
)
from apps.users.permissions import deny, get_client_ip, get_user_permissions, has_permission
from apps.utils.crypto import encrypt, hash_value

logger = logging.getLogger(__name__)

# 申请身份 -> 审核通过后授予的角色编码
APPLY_ROLE_TO_ROLE = {
    "doctor": "doctor",
    "pharmacist": "pharmacist",
    "cashier": "cashier",
    "pharmacy": "pharmacy",
    "patient": "patient",
}

ROLE_LABEL = dict(APPLY_ROLE_CHOICES)


def _log(request, action_type, object_type, object_id, detail, result="success", operator=None):
    from apps.stats.models import OperationLog

    OperationLog.objects.create(
        operator=operator or request.user,
        action_type=action_type,
        object_type=object_type,
        object_id=str(object_id),
        detail=detail,
        ip=get_client_ip(request),
        result=result,
    )


def landing(request):
    """门户封面页：未登录展示，已登录按角色进入工作台。"""
    if request.user.is_authenticated:
        return redirect(reverse(services.resolve_landing_url_name(request.user)))
    return render(request, "landing.html")


def captcha_view(request):
    """图形验证码图片端点。"""
    return captcha.captcha_image(request)


def login_view(request):
    """账号密码登录，带图形验证码与失败锁定。"""
    if request.user.is_authenticated:
        return redirect(reverse(services.resolve_landing_url_name(request.user)))

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        # 验证码先校验：不然可以拿登录接口无限撞库
        if not captcha.verify(request, request.POST.get("captcha", "")):
            messages.error(request, "验证码不正确或已过期，请重新输入")
            return render(request, "users/login.html", {"username": username})

        user = services.find_user(username)
        if services.is_locked(user):
            messages.error(request, f"账号已锁定，请 {services.lock_remaining_minutes(user)} 分钟后重试")
            return render(request, "users/login.html", {"username": username})

        auth_user = authenticate(request, username=user.username if user else username, password=password)
        if auth_user is None:
            if services.register_failure(user):
                messages.error(request, "连续失败次数过多，账号已锁定 10 分钟")
            else:
                messages.error(request, "用户名或密码错误")
            return render(request, "users/login.html", {"username": username})

        if auth_user.apply_status == "REJECTED":
            messages.error(request, "该账号的注册资料已被驳回，请联系医务科复核")
            return render(request, "users/login.html", {"username": username})

        services.reset_failures(user)
        login(request, auth_user)
        _log(request, "LOGIN", "session", auth_user.id, "用户登录", operator=auth_user)
        return redirect(reverse(services.resolve_landing_url_name(auth_user)))

    return render(request, "users/login.html")


def _parse_birth(raw):
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def register_view(request):
    """注册新账号，采集完整注册者信息。

    - 患者：注册即建档可用（自动生成病案号并绑定患者档案）；
    - 医师/药师/收费/药房：提交资质信息后进入「待审核」，由管理员审核
      通过才分配角色并建立执业档案，对应医疗机构岗位准入要求。
    """
    if request.user.is_authenticated:
        return redirect(reverse(services.resolve_landing_url_name(request.user)))

    from apps.patients.models import Patient

    context = {
        "form": {},
        "role_choices": APPLY_ROLE_CHOICES,
        "constitution_choices": Patient.CONSTITUTION_CHOICES,
        "pregnancy_choices": Patient.PREGNANCY_CHOICES,
    }

    if request.method != "POST":
        return render(request, "users/register.html", context)

    post = request.POST
    form = {key: post.get(key, "") for key in post.keys()}
    context["form"] = form

    username = post.get("username", "").strip()
    full_name = post.get("full_name", "").strip()
    password = post.get("password", "")
    password2 = post.get("password2", "")
    phone = post.get("phone", "").strip()
    email = post.get("email", "").strip()
    gender = post.get("gender", "U")
    apply_role = post.get("apply_role", "patient")
    id_card = post.get("id_card", "").strip().upper()

    if apply_role not in dict(APPLY_ROLE_CHOICES):
        apply_role = "patient"
    context["selected_role"] = apply_role

    def fail(message):
        messages.error(request, message)
        return render(request, "users/register.html", context)

    if not captcha.verify(request, post.get("captcha", "")):
        return fail("验证码不正确或已过期，请重新输入")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,19}", username):
        return fail("用户名需以字母开头，3~20 位字母、数字或下划线")
    if User.objects.filter(username__iexact=username).exists():
        return fail("该用户名已被注册，请更换")
    if not full_name or len(full_name) > 32:
        return fail("请填写真实姓名（不超过 32 个字符）")
    if gender not in ("M", "F", "U"):
        return fail("请选择性别")
    if not re.fullmatch(r"1[3-9]\d{9}", phone):
        return fail("请输入正确的 11 位手机号")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email):
        return fail("邮箱格式不正确")
    if len(password) < 8:
        return fail("密码长度不能少于 8 位")
    if password != password2:
        return fail("两次输入的密码不一致")
    if id_card and not re.fullmatch(r"\d{17}[\dX]", id_card):
        return fail("身份证号应为 18 位（末位可为 X）")
    if id_card and User.objects.filter(id_card_hash=hash_value(id_card)).exists():
        return fail("该身份证号已注册过账号")
    if id_card and Patient.objects.filter(id_card_hash=hash_value(id_card), is_deleted=False).exists():
        return fail("该身份证号已有院内病案，请到窗口办理账号绑定")

    # 医护/窗口岗位的资质必填校验
    license_no = post.get("license_no", "").strip()
    department = post.get("department", "").strip()
    title = post.get("title", "").strip()
    if apply_role in ("doctor", "pharmacist") and not license_no:
        return fail("医师/药师注册须填写执业证或资格证号，供医务科核验")
    if apply_role in ("doctor", "pharmacist", "pharmacy") and not department:
        return fail("请填写所属科室或部门")

    birth_date = _parse_birth(post.get("birth_date"))
    if post.get("birth_date") and not birth_date:
        return fail("出生日期格式不正确")

    user = User.objects.create_user(
        username=username,
        password=password,
        first_name=full_name,
        email=email,
        phone=phone,
    )
    user.gender = gender
    user.birth_date = birth_date
    user.address = post.get("address", "").strip()[:200]
    user.emergency_contact = post.get("emergency_contact", "").strip()[:64]
    user.emergency_phone = post.get("emergency_phone", "").strip()[:20]
    user.apply_role = apply_role
    user.license_no = license_no[:64]
    user.department = department[:64]
    user.title = title[:32]
    user.institution = post.get("institution", "").strip()[:128]
    user.practice_years = _safe_int(post.get("practice_years"))
    user.education = post.get("education", "").strip()[:32]
    user.apply_note = post.get("apply_note", "").strip()[:300]
    if id_card:
        user.id_card_encrypted = encrypt(id_card)
        user.id_card_hash = hash_value(id_card)
    # 患者注册即可用；医护/窗口岗位进入待审核
    user.apply_status = "NONE" if apply_role == "patient" else "PENDING"
    user.save()

    _log(request, "REGISTER", "user", user.id, f"注册账号 {username}（{ROLE_LABEL.get(apply_role)}）", operator=user)

    if apply_role == "patient":
        patient_no = Patient.generate_patient_no()
        Patient.objects.create(
            patient_no=patient_no,
            record_no=f"BA{patient_no}",
            name=full_name,
            gender=gender,
            age=user.age,
            user=user,
            id_card_encrypted=encrypt(id_card) if id_card else "",
            id_card_hash=hash_value(id_card) if id_card else "",
            phone_encrypted=encrypt(phone),
            phone_hash=hash_value(phone),
            address=post.get("address", "").strip()[:200],
            emergency_contact=post.get("emergency_contact", "").strip()[:64],
            emergency_phone=post.get("emergency_phone", "").strip()[:20],
            allergy_history=post.get("allergy_history", "").strip(),
            medical_history=post.get("medical_history", "").strip(),
            constitution=post.get("constitution", "BALANCED") or "BALANCED",
            pregnancy_status=post.get("pregnancy_status", "NONE") or "NONE",
            source="SELF",
        )
        patient_role, _ = Role.objects.get_or_create(
            code="patient", defaults={"name": "患者", "description": "查询本人处方与就诊记录"}
        )
        UserRole.objects.get_or_create(user=user, role=patient_role)
        login(request, user)
        messages.success(request, f"注册成功，已为您建立病案档案（病案号 {patient_no}）")
        return redirect(reverse(services.resolve_landing_url_name(user)))

    messages.success(request, "注册资料已提交，医务科审核通过后即可登录使用（一般 1 个工作日内）")
    return redirect("users:login")


def _safe_int(raw, default=None):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@login_required
def pending_review(request):
    """待审核/已驳回账号的状态页，避免这类账号登录后无处可去。"""
    return render(request, "users/pending.html")


@login_required
def application_list(request):
    """管理员审核注册者资料。"""
    if not has_permission(request.user, "用户:审核"):
        return deny(request, "仅管理员可审核注册资料", perm_code="用户:审核")

    status = request.GET.get("status", "PENDING")
    qs = User.objects.filter(apply_role__in=[code for code, _ in APPLY_ROLE_CHOICES])
    if status:
        qs = qs.filter(apply_status=status)
    keyword = request.GET.get("q", "").strip()
    if keyword:
        from django.db.models import Q

        qs = qs.filter(
            Q(username__icontains=keyword) | Q(first_name__icontains=keyword) | Q(phone__icontains=keyword)
        )
    if request.GET.get("role"):
        qs = qs.filter(apply_role=request.GET["role"])

    counts = {
        "PENDING": User.objects.filter(apply_status="PENDING").count(),
        "APPROVED": User.objects.filter(apply_status="APPROVED").count(),
        "REJECTED": User.objects.filter(apply_status="REJECTED").count(),
    }
    page = Paginator(qs.order_by("-date_joined"), 15).get_page(request.GET.get("page", 1))
    return render(request, "users/application_list.html", {
        "page": page,
        "applications": page.object_list,
        "status": status,
        "keyword": keyword,
        "counts": counts,
        "role_choices": APPLY_ROLE_CHOICES,
        "role_filter": request.GET.get("role", ""),
    })


@login_required
@require_POST
def application_review(request, pk):
    """通过/驳回注册申请；通过时同步建立执业档案并授予角色。"""
    if not has_permission(request.user, "用户:审核"):
        return deny(request, "仅管理员可审核注册资料", perm_code="用户:审核")

    target = get_object_or_404(User, pk=pk)
    action = request.POST.get("action")
    note = (request.POST.get("review_note") or "").strip()[:300]

    if action == "approve":
        role_code = APPLY_ROLE_TO_ROLE.get(target.apply_role)
        role = Role.objects.filter(code=role_code).first() if role_code else None
        if not role:
            messages.error(request, "未找到对应角色，请先初始化角色数据")
            return redirect("users:application_list")
        UserRole.objects.get_or_create(user=target, role=role)

        if target.apply_role == "doctor":
            Doctor.objects.get_or_create(
                user=target,
                defaults={
                    "license_no": target.license_no or f"ZY{target.id:06d}",
                    "department": target.department or "中医科",
                    "title": target.title or "主治医师",
                },
            )
        elif target.apply_role == "pharmacist":
            Pharmacist.objects.get_or_create(
                user=target, defaults={"license_no": target.license_no or f"YS{target.id:06d}"}
            )
        target.apply_status = "APPROVED"
        messages.success(request, f"已通过 {target.display_name} 的注册申请，角色：{role.name}")
    elif action == "reject":
        target.apply_status = "REJECTED"
        messages.warning(request, f"已驳回 {target.display_name} 的注册申请")
    else:
        messages.error(request, "未知的审核操作")
        return redirect("users:application_list")

    target.reviewed_by = request.user
    target.reviewed_at = timezone.now()
    target.review_note = note
    target.save(update_fields=["apply_status", "reviewed_by", "reviewed_at", "review_note"])
    _log(request, action.upper(), "user", target.id, f"注册审核 {target.username} -> {target.apply_status}")
    return redirect(request.META.get("HTTP_REFERER") or "users:application_list")


def logout_view(request):
    """安全退出，销毁会话并记录日志。"""
    if request.user.is_authenticated:
        _log(request, "LOGOUT", "session", request.user.id, "用户退出")
    logout(request)
    return redirect("users:login")


@require_POST
def api_login(request):
    """API 登录接口：POST /api/v1/auth/login。

    与页面登录共用锁定策略，避免通过接口绕过失败计数。
    """
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"code": 400, "message": "请求体不是合法 JSON"}, status=400)

    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    user = services.find_user(username)
    if services.is_locked(user):
        return JsonResponse(
            {"code": 423, "message": f"账号已锁定，请 {services.lock_remaining_minutes(user)} 分钟后重试"},
            status=423,
        )

    auth_user = authenticate(request, username=user.username if user else username, password=password)
    if auth_user is None:
        services.register_failure(user)
        return JsonResponse({"code": 401, "message": "用户名或密码错误"}, status=401)

    services.reset_failures(user)
    login(request, auth_user)
    _log(request, "LOGIN", "session", auth_user.id, "API 登录", operator=auth_user)
    return JsonResponse({
        "code": 200,
        "message": "ok",
        "data": {
            "id": auth_user.id,
            "username": auth_user.username,
            "name": auth_user.get_full_name(),
            "landing": reverse(services.resolve_landing_url_name(auth_user)),
        },
    })


@require_POST
def api_logout(request):
    """API 退出接口：POST /api/v1/auth/logout。"""
    logout(request)
    return JsonResponse({"code": 200, "message": "ok", "data": None})


@login_required
def doctor_list(request):
    """医师列表与资料查询。"""
    if not has_permission(request.user, "用户:读"):
        return deny(request, perm_code="用户:读")
    doctors = Doctor.objects.select_related("user").filter(is_active=True).order_by("department", "user__first_name")
    return render(request, "users/doctor_list.html", {"doctors": doctors})


def home_redirect(request):
    """根路径：未登录看门户封面，已登录按角色跳转到各自的工作台。"""
    if not request.user.is_authenticated:
        return redirect("users:landing")
    return redirect(reverse(services.resolve_landing_url_name(request.user)))


@login_required
def profile_view(request):
    """个人中心：本人注册资料、角色权限与审核状态。"""
    roles = list(UserRole.objects.filter(user=request.user).values_list("role__name", "role__description"))
    perms = sorted(get_user_permissions(request.user))
    return render(request, "users/profile.html", {
        "roles": roles,
        "perms": perms,
        "is_locked": services.is_locked(request.user),
        "lock_minutes": services.lock_remaining_minutes(request.user),
        "apply_role_label": ROLE_LABEL.get(request.user.apply_role, "患者"),
        "apply_status_label": dict(APPLY_STATUS_CHOICES).get(request.user.apply_status, "—"),
    })


def api_doctors(request):
    """GET /api/v1/doctors 医师列表。"""
    if not request.user.is_authenticated:
        return JsonResponse({"code": 401, "message": "未登录"}, status=401)
    if not has_permission(request.user, "用户:读"):
        return JsonResponse({"code": 403, "message": "无权限"}, status=403)
    doctors = list(
        Doctor.objects.select_related("user")
        .filter(is_active=True)
        .values("id", "user__username", "user__first_name", "license_no", "department", "title")
    )
    return JsonResponse({"code": 200, "message": "ok", "data": doctors})


@login_required
def role_permission_manage(request):
    """角色、菜单与接口访问权限配置页（RBAC 管理界面）。

    对应用户与医师管理子功能③：以角色为单位勾选权限，权限点按
    菜单/操作/接口三层分组展示，保存时计算增减差异并写操作日志留痕。
    """
    if not has_permission(request.user, "权限:配置"):
        return deny(request, perm_code="权限:配置")

    roles = Role.objects.all().order_by("id")
    all_perms = Permission.objects.all().order_by("level", "id")

    # 当前编辑的角色：?role=<id>，默认取第一个角色
    roles_map = {r.id: r for r in roles}
    role_id = None
    try:
        role_id = int(request.GET.get("role") or request.POST.get("role") or 0)
    except (TypeError, ValueError):
        role_id = 0
    current_role = roles_map.get(role_id) or (roles[0] if roles else None)

    if request.method == "POST" and current_role:
        selected = set()
        for key in request.POST.getlist("perms"):
            try:
                selected.add(int(key))
            except (TypeError, ValueError):
                continue

        valid_perm_ids = set(all_perms.values_list("id", flat=True))
        selected &= valid_perm_ids

        old_ids = set(RolePermission.objects.filter(role=current_role).values_list("permission_id", flat=True))
        added = selected - old_ids
        removed = old_ids - selected

        if added or removed or request.POST.get("force_save"):
            with transaction.atomic():
                if added:
                    RolePermission.objects.bulk_create(
                        [RolePermission(role=current_role, permission_id=pid) for pid in added]
                    )
                if removed:
                    RolePermission.objects.filter(role=current_role, permission_id__in=removed).delete()

                added_names = list(Permission.objects.filter(id__in=added).values_list("code", flat=True))
                removed_names = list(Permission.objects.filter(id__in=removed).values_list("code", flat=True))
                from apps.stats.models import OperationLog

                OperationLog.objects.create(
                    operator=request.user, action_type="UPDATE", object_type="role_permission",
                    object_id=str(current_role.id),
                    detail=f"调整角色「{current_role.name}」权限：新增 {added_names or '无'}；移除 {removed_names or '无'}",
                    ip=get_client_ip(request), result="success",
                )
                messages.success(
                    request,
                    f"角色「{current_role.name}」权限已保存：新增 {len(added)} 项，移除 {len(removed)} 项，变更已留痕。",
                )
        else:
            messages.info(request, "权限未发生变化。")
        return redirect(f"{reverse('users:role_permissions')}?role={current_role.id}")

    # 权限按层级分组渲染；勾选当前角色已有权限
    granted = set()
    if current_role:
        granted = set(RolePermission.objects.filter(role=current_role).values_list("permission_id", flat=True))
    perm_groups = []
    for level, label in Permission.LEVEL_CHOICES:
        perms = [p for p in all_perms if p.level == level]
        if perms:
            perm_groups.append({
                "level": level,
                "label": label,
                "items": [{"obj": p, "checked": p.id in granted} for p in perms],
            })

    role_stats = {
        r.id: RolePermission.objects.filter(role=r).count() for r in roles
    }
    for r in roles:
        r.perm_count = role_stats[r.id]
    return render(request, "users/role_permissions.html", {
        "roles": roles,
        "role_stats": role_stats,
        "current_role": current_role,
        "perm_groups": perm_groups,
    })
