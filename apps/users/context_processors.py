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
        ("workbench", "诊室工作台", "clinic:workbench", "bi-person-workspace", "接诊:读"),
    ]),
    ("门诊业务", [
        ("registration_list", "挂号与接诊", "clinic:registration_list", "bi-calendar2-check", "挂号:管理"),
        ("request_list", "病案服务", "clinic:request_list", "bi-folder2-open", "病历申请:处理"),
        ("my_visits", "我的就诊", "clinic:my_visits", "bi-clipboard2-heart", "我的就诊:读"),
        ("my_prescriptions", "我的处方", "prescriptions:my_prescriptions", "bi-person-vcard", "我的处方:读"),
    ]),
    ("处方与审核", [
        ("prescription_list", "处方管理", "prescriptions:prescription_list", "bi-journal-medical", "处方:新增"),
        ("workbench", "审核工作台", "reviews:workbench", "bi-clipboard2-check", "审核:读"),
        ("payment_queue", "缴费登记", "prescriptions:payment_queue", "bi-cash-coin", "收费:确认"),
        ("dispense_queue", "发药窗口", "prescriptions:dispense_queue", "bi-box-seam", "发药:确认"),
    ]),
    ("档案与字典", [
        ("patient_list", "患者档案", "patients:patient_list", "bi-people", "患者:读"),
        ("herb_list", "药材字典", "prescriptions:herb_list", "bi-flower2", "字典:维护"),
    ]),
    ("系统", [
        ("application_list", "注册审核", "users:application_list", "bi-person-check", "用户:审核"),
        ("role_permissions", "角色权限配置", "users:role_permissions", "bi-shield-lock", "权限:配置"),
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
        seen = set()
        for key, label, url_name, icon, perm in items:
            if perm and perm not in perms:
                continue
            namespace, _, name = url_name.partition(":")
            # 同一 key 在不同 app 下重名（如 clinic/reviews 都有 workbench），
            # 必须连命名空间一起比，否则两个菜单会同时高亮
            if (namespace, name) in seen:
                continue
            seen.add((namespace, name))
            visible.append({
                "key": key,
                "label": label,
                "url_name": url_name,
                "url": _safe_reverse(url_name),
                "icon": icon,
                "active": current_ns == namespace and current_url_name == name,
            })
        if visible:
            groups.append({"name": group_name, "items": visible})

    from apps.reviews.models import Notification

    return {
        "nav_groups": groups,
        "nav_current_ns": current_ns,
        "user_perms": perms,
        "unread_count": Notification.objects.filter(recipient=user, is_read=False).count(),
    }


def _safe_reverse(url_name):
    """反向解析失败时返回空串，避免菜单渲染导致整页 500。"""
    from django.urls import NoReverseMatch, reverse

    try:
        return reverse(url_name)
    except NoReverseMatch:
        return ""
