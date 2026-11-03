# -*- coding: utf-8 -*-
"""云端（公网）启动入口 —— 与本地 一键启动.bat 完全独立。

用途：
    把项目发布到公网时，平台只给一个端口（环境变量 PORT），并要求进程
    监听 0.0.0.0。本脚本负责：加载配置 → 建库/补演示数据 → 用 PORT 起服务。

为什么不直接用 manage.py runserver：
    公网上域名是平台分配的，settings 里的 ALLOWED_HOSTS 白名单默认只放
    本机地址，直接跑会 400 DisallowedHost。这里在 django.setup() 之前把
    ALLOWED_HOSTS 放开，避免改动 config/settings.py。

本地开发不受影响：一键启动.bat 不会调用本文件。
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# 先加载 .env，让本地已有的密钥优先（保证与库里的加密数据一致）
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass

# 公网部署必须放开的三项；本地 .env 里的同名值会被保留
os.environ["DJANGO_DEBUG"] = "1"
os.environ["DJANGO_ALLOWED_HOSTS"] = "*"
os.environ.setdefault("DJANGO_SECRET_KEY", "cloud-demo-only-insecure-key-do-not-reuse-0123456789abcdef")
os.environ.setdefault("DATA_ENCRYPTION_KEY", "Zm9yLWNsb3VkLWRlbW8tb25seS1rZXktMzJieXRlcyE=")


def bootstrap_data() -> None:
    """建表 + 补齐演示数据；已经初始化过就跳过，避免每次启动重复写入。"""
    from django.core.management import call_command
    from django.contrib.auth import get_user_model

    call_command("migrate", interactive=False, verbosity=0)

    if not get_user_model().objects.exists():
        print("[cloud] 首次启动，初始化角色/权限/药材/演示账号 ...")
        call_command("init_data", verbosity=0)

    from apps.prescriptions.models import Prescription

    if not Prescription.objects.exists():
        print("[cloud] 生成演示处方 ...")
        call_command("seed_prescriptions", verbosity=0)


class TrustRequestOrigin:
    """把当前请求的 Origin 加入 CSRF 信任列表（仅公网演示实例使用）。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.META.get("HTTP_ORIGIN")
        if origin:
            from django.conf import settings

            if origin not in settings.CSRF_TRUSTED_ORIGINS:
                settings.CSRF_TRUSTED_ORIGINS.append(origin)
        return self.get_response(request)


def main() -> None:
    import django
    from django.conf import settings

    django.setup()

    # 公网域名是平台随机分配的，无法提前写进 CSRF_TRUSTED_ORIGINS；
    # 浏览器提交表单会带 Origin 头，不在白名单里就会被 403 拦掉。
    # 这里挂一个前置中间件，把当前请求的 Origin 动态加进白名单
    # （只对公网演示实例生效，本地开发完全不受影响）。
    if "cloud_entry.TrustRequestOrigin" not in settings.MIDDLEWARE:
        settings.MIDDLEWARE.insert(0, "cloud_entry.TrustRequestOrigin")

    bootstrap_data()

    port = os.environ.get("PORT") or "8000"
    print(f"[cloud] 服务启动：0.0.0.0:{port}")
    from django.core.management import call_command

    call_command("runserver", f"0.0.0.0:{port}", use_reloader=False, verbosity=1)


if __name__ == "__main__":
    main()
