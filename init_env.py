# -*- coding: utf-8 -*-
"""首次运行时生成 .env 配置文件。

为什么要这个脚本：
    .env 被 .gitignore 忽略，从 GitHub clone 下来的项目里没有它。
    而 config/settings.py 采用 fail-closed 策略：
        DEBUG 缺省 False -> SECRET_KEY 为空 -> 直接抛 ImproperlyConfigured
    所以新机器上必然启动失败。这里按开发环境所需的最小配置生成一份，
    已经存在 .env 时不动它（避免覆盖用户自己配的密钥）。
"""
import os
import secrets
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE, ".env")

TEMPLATE = """# ===== Django 基础 =====
# 本机开发配置，由 init_env.py 自动生成；生产环境请另行配置真实密钥
DJANGO_SECRET_KEY={secret}
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,0.0.0.0
DJANGO_CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000

# ===== 数据存储 =====
# 留空使用 SQLite；要用 MySQL 时改为 mysql 并填下面几项
DB_ENGINE=
DB_NAME=tcm_rms
DB_USER=tcm
DB_PASSWORD=
DB_HOST=127.0.0.1
DB_PORT=3306
REDIS_URL=

# ===== 安全 =====
# 患者身份证号 / 手机号等敏感字段的加密密钥，缺失时无法解密已有数据
DATA_ENCRYPTION_KEY={fernet}
TRUST_X_FORWARDED_FOR=False
LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCK_SECONDS=600
"""


def main() -> int:
    if os.path.exists(ENV_PATH):
        print("[env] .env 已存在，跳过生成")
        return 0

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        print("[env] 错误：cryptography 未安装，无法生成 DATA_ENCRYPTION_KEY")
        return 1

    content = TEMPLATE.format(
        secret=secrets.token_urlsafe(64),
        fernet=Fernet.generate_key().decode(),
    )
    with open(ENV_PATH, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(content)
    print("[env] 已生成 .env（DJANGO_DEBUG=True，密钥随机生成）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
