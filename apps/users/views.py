"""用户登录、注册、退出与医师管理视图。"""
import json
import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.users import services
from apps.users.models import Doctor, Role, User, UserRole
from apps.users.permissions import deny, get_client_ip, get_user_permissions, has_permission

logger = logging.getLogger(__name__)


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


def login_view(request):
    """账号密码登录，带登录失败锁定。"""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        user = services.find_user(username)
        if services.is_locked(user):
            messages.error(request, f"账号已锁定，请 {services.lock_remaining_minutes(user)} 分钟后重试")
            return render(request, "users/login.html")

        auth_user = authenticate(request, username=user.username if user else username, password=password)
        if auth_user is None:
            if services.register_failure(user):
                messages.error(request, "连续失败次数过多，账号已锁定 10 分钟")
            else:
                messages.error(request, "用户名或密码错误")
            return render(request, "users/login.html")

        services.reset_failures(user)
        login(request, auth_user)
        _log(request, "LOGIN", "session", auth_user.id, "用户登录", operator=auth_user)
        return redirect(reverse(services.resolve_landing_url_name(auth_user)))

    return render(request, "users/login.html")


def register_view(request):
    """注册新账号（默认分配"患者"角色）。

    医师、药师角色因涉及法定资质绑定，须由管理员在后台分配，
    公开注册仅开放患者角色，符合权限设计的最小权限原则。
    """
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        full_name = request.POST.get("full_name", "").strip()
        phone = request.POST.get("phone", "").strip()
        password = request.POST.get("password", "")
        password2 = request.POST.get("password2", "")

        if not username:
            messages.error(request, "请输入用户名")
            return render(request, "users/register.html")
        if not full_name:
            messages.error(request, "请输入姓名")
            return render(request, "users/register.html")
        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, "该用户名已被注册，请更换")
            return render(request, "users/register.html")
        if len(password) < 8:
            messages.error(request, "密码长度不能少于 8 位")
            return render(request, "users/register.html")
        if password != password2:
            messages.error(request, "两次输入的密码不一致")
            return render(request, "users/register.html")

        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=full_name,
            phone=phone,
        )

        patient_role, _ = Role.objects.get_or_create(
            code="patient", defaults={"name": "患者", "description": "查询本人处方"}
        )
        UserRole.objects.get_or_create(user=user, role=patient_role)

        _log(request, "REGISTER", "user", user.id, f"注册新账号 {username}", operator=user)

        login(request, user)
        messages.success(request, "注册成功，欢迎使用！")
        return redirect(reverse(services.resolve_landing_url_name(user)))

    return render(request, "users/register.html")


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
    """根路径按角色跳转到各自的工作台，避免患者登录后直接撞上"无权限"。"""
    if not request.user.is_authenticated:
        return redirect("users:login")
    return redirect(reverse(services.resolve_landing_url_name(request.user)))


@login_required
def profile_view(request):
    """个人中心：查看本人角色与权限，也是无业务权限时的兜底落地页。"""
    from apps.users.models import UserRole

    roles = list(UserRole.objects.filter(user=request.user).values_list("role__name", "role__description"))
    perms = sorted(get_user_permissions(request.user))
    return render(request, "users/profile.html", {
        "roles": roles,
        "perms": perms,
        "is_locked": services.is_locked(request.user),
        "lock_minutes": services.lock_remaining_minutes(request.user),
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
