"""图形验证码。

零第三方依赖：服务端直接生成 SVG 图片，不装 Pillow 也能跑，
避免 requirements 里再增加一个 Windows 编译困难的包。

安全约定：
- 答案只存 session，不下发到页面；
- 一次性使用（校验后立即 pop），防止重放；
- 3 分钟过期，过期即失效。
"""
import random
import time

from django.http import HttpResponse

# 去掉 I / O / 0 / 1 等易混字符，降低"看错验证码"的挫败感
CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SESSION_KEY = "login_captcha"
TTL_SECONDS = 180


def _new_code(length: int = 4) -> str:
    return "".join(random.choice(CHARS) for _ in range(length))


def issue_code(request) -> str:
    """生成验证码并写入 session。"""
    code = _new_code()
    request.session[SESSION_KEY] = {"code": code, "ts": int(time.time())}
    return code


def verify(request, raw: str) -> bool:
    """校验并立即销毁本次验证码。"""
    data = request.session.pop(SESSION_KEY, None)
    if not data or not raw:
        return False
    if int(time.time()) - int(data.get("ts", 0)) > TTL_SECONDS:
        return False
    return str(raw).strip().upper() == str(data.get("code", "")).upper()


def _svg(code: str) -> str:
    width, height = 132, 46
    palette = ["#0f3d2e", "#1f6f52", "#7d5a29", "#3f6d8f", "#8c3b2e"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="图形验证码">',
        '<rect width="100%" height="100%" fill="#f2f8f4"/>',
    ]

    # 背景干扰曲线
    for _ in range(3):
        x1, y1 = random.randint(0, 20), random.randint(6, height - 6)
        x2, y2 = random.randint(width - 20, width), random.randint(6, height - 6)
        cx, cy = random.randint(20, width - 20), random.randint(0, height)
        parts.append(
            f'<path d="M{x1},{y1} Q{cx},{cy} {x2},{y2}" fill="none" '
            f'stroke="{random.choice(palette)}" stroke-opacity="0.28" stroke-width="1.4"/>'
        )

    # 字符逐位旋转、轻微错位
    step = width / (len(code) + 1)
    for index, char in enumerate(code):
        x = step * (index + 1) + random.uniform(-2, 2)
        y = height / 2 + random.uniform(-3, 4)
        rotate = random.uniform(-22, 22)
        color = random.choice(palette)
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" fill="{color}" font-size="27" font-weight="700" '
            f'font-family="Microsoft YaHei, PingFang SC, Arial, sans-serif" '
            f'text-anchor="middle" dominant-baseline="middle" '
            f'transform="rotate({rotate:.1f} {x:.1f} {y:.1f})">{char}</text>'
        )

    # 噪点
    for _ in range(18):
        parts.append(
            f'<circle cx="{random.randint(0, width)}" cy="{random.randint(0, height)}" '
            f'r="{random.choice([0.7, 1, 1.3])}" fill="{random.choice(palette)}" fill-opacity="0.35"/>'
        )

    parts.append("</svg>")
    return "".join(parts)


def captcha_image(request) -> HttpResponse:
    """GET /captcha/ ：输出图片并刷新 session 中的答案。"""
    code = issue_code(request)
    response = HttpResponse(_svg(code), content_type="image/svg+xml; charset=utf-8")
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response
