"""敏感字段加解密工具。

设计要点：
- 对称加密（Fernet）密钥必须由 ``DATA_ENCRYPTION_KEY`` 环境变量注入；
  生产环境缺失时直接抛错（fail-closed），避免用源码里的默认密钥保护真实隐私。
- 开发环境允许从 ``SECRET_KEY`` 派生一把密钥，方便开箱即跑，但会打印告警。
- 等值查询用 **HMAC** 而非裸 SHA-256：身份证号结构高度可枚举，
  无盐哈希可以被预计算表反查，必须带密钥。
"""
import base64
import hashlib
import hmac
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

_warned = False


def _derive_key_from_secret(secret_key: str) -> bytes:
    """从 SECRET_KEY 稳定派生一把 Fernet 密钥（仅开发环境降级使用）。"""
    digest = hashlib.sha256(("tcm-rms-data-key:" + secret_key).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_key() -> bytes:
    """获取加密密钥；生产环境缺失即报错，不静默降级。"""
    global _warned
    key = os.environ.get("DATA_ENCRYPTION_KEY", "").strip()
    if key:
        return key.encode("utf-8")

    if not settings.DEBUG:
        raise ImproperlyConfigured(
            "生产环境必须设置 DATA_ENCRYPTION_KEY 环境变量（Fernet 密钥），生成方式："
            "python -c \"from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())\""
        )

    if not _warned:
        logger.warning("DATA_ENCRYPTION_KEY 未设置，已从 SECRET_KEY 派生开发用密钥（请勿用于生产）")
        _warned = True
    return _derive_key_from_secret(settings.SECRET_KEY)


def _get_hash_key() -> bytes:
    """获取 HMAC 哈希密钥，与加密密钥分离，便于分别轮换。"""
    key = os.environ.get("DATA_HASH_KEY", "").strip()
    if key:
        return key.encode("utf-8")
    return ("tcm-rms-hash:" + settings.SECRET_KEY).encode("utf-8")


def encrypt(text: str) -> str:
    """加密文本，返回密文字符串。"""
    if not text:
        return ""
    return Fernet(_get_key()).encrypt(text.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """解密密文，返回明文；密钥不匹配或数据损坏时返回空串而不抛错。"""
    if not token:
        return ""
    try:
        return Fernet(_get_key()).decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("敏感字段解密失败，可能是密钥已轮换或数据损坏")
        return ""


def mask_id_card(id_card: str) -> str:
    """身份证号掩码：保留前 3 位与后 4 位。"""
    if not id_card:
        return ""
    if len(id_card) <= 7:
        return id_card[0] + "*" * (len(id_card) - 1)
    return id_card[:3] + "*" * (len(id_card) - 7) + id_card[-4:]


def mask_phone(phone: str) -> str:
    """手机号掩码：中间 4 位以 * 代替。"""
    if not phone:
        return ""
    if len(phone) < 7:
        return phone[0] + "*" * (len(phone) - 1)
    return phone[:3] + "****" + phone[7:]


def hash_value(value: str) -> str:
    """对敏感字段做带密钥的哈希（HMAC-SHA256），用于等值查询与查重。"""
    if not value:
        return ""
    return hmac.new(_get_hash_key(), value.encode("utf-8"), hashlib.sha256).hexdigest()
