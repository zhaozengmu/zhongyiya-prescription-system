"""RBAC 权限校验工具。

提供接口级权限装饰器与角色判断辅助函数，
实现"菜单—操作—接口"三级权限控制。

权限集合按 user 实例缓存，避免一次请求里反复查库
（导航菜单、页面、接口可能各自校验一次）。
"""
from functools import wraps

from django.http import JsonResponse

_CACHE_ATTR = "_perms_cache"


def get_user_roles(user):
    """获取用户的所有角色编码。"""
    if not user or not user.is_authenticated:
        return set()
    return set(user.userrole_set.values_list("role__code", flat=True))


def get_user_permissions(user):
    """获取用户的所有权限编码（同一 user 实例内缓存）。"""
    if not user or not user.is_authenticated:
        return set()

    cached = getattr(user, _CACHE_ATTR, None)
    if cached is not None:
        return cached

    from apps.users.models import Permission, RolePermission

    perms = set(
        RolePermission.objects.filter(role__userrole__user=user).values_list("permission__code", flat=True)
    )
    # 超级用户拥有全部权限，避免后台建的管理员被权限表卡住
    if user.is_superuser:
        perms |= set(Permission.objects.values_list("code", flat=True))

    setattr(user, _CACHE_ATTR, perms)
    return perms


def has_permission(user, perm_code):
    """判断用户是否拥有某权限。"""
    return perm_code in get_user_permissions(user)


def has_any_permission(user, *perm_codes):
    """拥有任意一个权限即通过。"""
    perms = get_user_permissions(user)
    return any(code in perms for code in perm_codes)


def permission_required(perm_code):
    """接口级权限装饰器。

    校验失败返回 403 并记入日志，避免"隐藏按钮但接口可调用"的越权漏洞。
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({"code": 401, "message": "未登录或会话过期"}, status=401)
            if not has_permission(request.user, perm_code):
                from apps.stats.models import OperationLog

                OperationLog.objects.create(
                    operator=request.user,
                    action_type="ACCESS_DENIED",
                    object_type="api",
                    object_id=perm_code,
                    detail=f"越权访问接口：{perm_code}",
                    ip=get_client_ip(request),
                    result="denied",
                )
                return JsonResponse({"code": 403, "message": "无权限访问"}, status=403)
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def deny(request, message="", perm_code=""):
    """页面级越权响应：渲染统一的无权限页并返回 **403**。

    之前各视图直接 ``render(...)`` 返回 200，数据虽未泄露，
    但状态码语义错误——监控、爬虫与前端拦截都无法区分"正常空页"与"越权"。
    """
    from django.shortcuts import render

    from apps.stats.models import OperationLog

    if perm_code:
        try:
            OperationLog.objects.create(
                operator=request.user if request.user.is_authenticated else None,
                action_type="ACCESS_DENIED",
                object_type="page",
                object_id=perm_code,
                detail=f"越权访问页面：{perm_code}",
                ip=get_client_ip(request),
                result="denied",
            )
        except Exception:  # 审计失败不应阻断拒绝响应
            pass

    return render(
        request,
        "no_permission.html",
        {"deny_message": message, "deny_perm": perm_code},
        status=403,
    )


def get_client_ip(request):
    """获取客户端 IP。

    仅当显式开启 TRUST_X_FORWARDED_FOR 时才信任该头；
    否则它可以被客户端随意伪造，导致审计日志 IP 失真。
    """
    from django.conf import settings

    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def parse_json_body(request):
    """安全解析请求体，返回 (data, error_response)。"""
    import json

    if not request.body:
        return {}, None
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return None, JsonResponse({"code": 400, "message": "请求体不是合法 JSON"}, status=400)
    if not isinstance(data, dict):
        return None, JsonResponse({"code": 400, "message": "请求体必须是 JSON 对象"}, status=400)
    return data, None
