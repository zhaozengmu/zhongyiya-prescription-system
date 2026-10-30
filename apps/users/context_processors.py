"""导航菜单上下文处理器。

菜单按 RBAC 权限动态渲染并高亮当前模块，避免"所有角色看到同一个菜单、
点进去才发现无权限"的体验问题。
"""
from apps.users.permissions import get_user_permissions

# 菜单结构：(分组名, [(key, 名称, URL 名, 图标, 所需权限)])
# 所需权限为空表示登录即可见
NAV_GROUPS = [
    ("工作台", [
        ("dashboard", "管理驾驶舱", "stats:dashboard", "bi-speedometer2", "统计:读"),
    ]),
    ("业务处理", [
        ("prescription_list", "处方管理", "prescriptions:prescription_list", "bi-journal-medical", "处方:新增"),
        ("workbench", "审核工作台", "reviews:workbench", "bi-clipboard2-check", "审核:读"),
        ("payment_queue", "缴费登记", "prescriptions:payment_queue", "bi-cash-coin", "收费:确认"),
        ("dispense_queue", "发药窗口", "prescriptions:dispense_queue", "bi-box-seam", "发药:确认"),
        ("my_prescriptions", "我的处方", "prescriptions:my_prescriptions", "bi-person-vcard", "我的处方:读"),
    ]),
    ("档案与字典", [
        ("patient_list", "患者档案", "patients:patient_list", "bi-people", "患者:读"),
        ("herb_list", "药材字典", "prescriptions:herb_list", "bi-flower2", "字典:维护"),
    ]),
    ("系统", [
        ("logs", "操作日志", "stats:logs", "bi-list-check", "日志:读"),
    ]),
]


def nav_context(request):
    """注入导航分组、当前菜单项与未读消息数。"""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    perms = get_user_permissions(user)
    match = getattr(request, "resolver_match", None)
    current_ns = match.namespace if match else ""
    current_url_name = match.url_name if match else ""

    groups = []
    for group_name, items in NAV_GROUPS:
        visible = []
        for key, label, url_name, icon, perm in items:
            if perm and perm not in perms:
                continue
            visible.append({
                "key": key,
                "label": label,
                "url_name": url_name,
                "url": _safe_reverse(url_name),
                "icon": icon,
                "active": current_url_name == key,
            })
        if visible:
            groups.append({"name": group_name, "items": visible})

    from apps.reviews.models import Notification

    return {
        "nav_groups": groups,
        "nav_current_ns": current_ns,
        "unread_count": Notification.objects.filter(recipient=user, is_read=False).count(),
    }


def _safe_reverse(url_name):
    """反向解析失败时返回空串，避免菜单渲染导致整页 500。"""
    from django.urls import NoReverseMatch, reverse

    try:
        return reverse(url_name)
    except NoReverseMatch:
        return ""
