"""用户登录锁定与落地页解析。

把「连续失败锁定」从视图里抽出来，Web 表单登录与 API 登录共用同一套逻辑，
避免出现「页面会锁、接口不锁」的绕过口子。
"""
from django.conf import settings
from django.utils import timezone

from apps.users.models import User


def is_locked(user) -> bool:
    """账号是否处于锁定期内。"""
    return bool(user and user.locked_until and user.locked_until > timezone.now())


def lock_remaining_minutes(user) -> int:
    """剩余锁定分钟数（向上取整，至少 1）。"""
    if not is_locked(user):
        return 0
    delta = user.locked_until - timezone.now()
    return max(1, int(delta.total_seconds() // 60) + (1 if delta.total_seconds() % 60 else 0))


def reset_failures(user) -> None:
    """登录成功后清空失败计数与锁定时间。"""
    if not user:
        return
    if user.failed_attempts or user.locked_until:
        user.failed_attempts = 0
        user.locked_until = None
        user.save(update_fields=["failed_attempts", "locked_until"])


def register_failure(user) -> bool:
    """记录一次失败；返回本次是否触发了锁定。

    注意：触发锁定的分支同样必须落库，否则锁定状态会随请求结束丢掉。
    """
    if not user:
        return False
    user.failed_attempts = (user.failed_attempts or 0) + 1
    locked = False
    if user.failed_attempts >= settings.LOGIN_MAX_ATTEMPTS:
        user.locked_until = timezone.now() + timezone.timedelta(seconds=settings.LOGIN_LOCK_SECONDS)
        user.failed_attempts = 0
        locked = True
    user.save(update_fields=["failed_attempts", "locked_until"])
    return locked


def find_user(username: str):
    """按用户名查用户（大小写不敏感，避免绕过失败计数）。"""
    if not username:
        return None
    return User.objects.filter(username__iexact=username.strip()).first()


def resolve_landing_url_name(user) -> str:
    """按角色权限决定登录后的落地页，避免"登录即无权限"的死角。"""
    from apps.users.permissions import get_user_permissions

    perms = get_user_permissions(user)
    if "统计:读" in perms:
        return "stats:dashboard"
    if "审核:读" in perms:
        return "reviews:workbench"
    if "发药:确认" in perms:
        return "prescriptions:dispense_queue"
    if "收费:确认" in perms:
        return "prescriptions:payment_queue"
    if "处方:新增" in perms:
        return "prescriptions:prescription_list"
    if "患者:读" in perms:
        return "patients:patient_list"
    if "我的处方:读" in perms:
        return "prescriptions:my_prescriptions"
    return "users:profile"
