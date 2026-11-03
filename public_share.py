"""公网手机访问启动器。

做三件事：
    1. 在独立端口（默认 8001）拉起一个"公网模式"的 Django 实例，
       与本地 8000 的一键启动互不干扰；
    2. 启动 cloudflared 快速隧道，把该端口映射成 https 公网地址；
    3. 抓到公网链接后大字打印、存到桌面、并复制到剪贴板。

链接是临时的：关掉本窗口或电脑休眠即失效，重新运行会拿到一个新地址。
"""
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOCAL_PORT = int(os.environ.get("PUBLIC_PORT", "8001"))
CLOUDFLARED = Path(os.environ.get("CLOUDFLARED_EXE", Path.home() / ".cloudflared" / "cloudflared.exe"))
CLOUDFLARED_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

try:
    # 只有在真实控制台（cp936 窗口）里才切 GBK；
    # 输出被重定向/走管道时保持 UTF-8，否则中文会变成乱码。
    if sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="gbk", errors="replace")
except Exception:
    pass


def log(msg: str) -> None:
    print(msg, flush=True)


def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_ready(port: int, seconds: int = 90) -> bool:
    deadline = time.time() + seconds
    url = f"http://127.0.0.1:{port}/login/"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def ensure_cloudflared() -> bool:
    if CLOUDFLARED.exists():
        return True
    log("[1/3] 未找到 cloudflared，正在下载（约 55MB，只需一次）...")
    CLOUDFLARED.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(CLOUDFLARED_URL, timeout=180) as resp:
            data = resp.read()
        CLOUDFLARED.write_bytes(data)
        log(f"      已保存到 {CLOUDFLARED}")
        return True
    except Exception as exc:
        log(f"[错误] 下载失败：{exc}")
        return False


def copy_to_clipboard(text: str) -> None:
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{text}'"],
            timeout=10,
            check=False,
            capture_output=True,
        )
    except Exception:
        pass


def desktop_dir() -> Path:
    """取真实桌面路径（本机桌面被重定向到 D:\\桌面，不能想当然用 ~/Desktop）。"""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        )
        raw, _ = winreg.QueryValueEx(key, "Desktop")
        return Path(os.path.expandvars(raw))
    except Exception:
        return Path.home() / "Desktop"


def save_link(url: str) -> None:
    target = desktop_dir() / "公网访问链接.txt"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(url + "\n", encoding="utf-8")
        log(f"      链接已保存到：{target}")
    except Exception as exc:
        log(f"      （链接保存失败：{exc}，直接复制上面那行即可）")


def main() -> int:
    log("=" * 62)
    log("      中医处方开具与审核管理系统 —— 手机公网访问")
    log("=" * 62)
    log("")

    if not ensure_cloudflared():
        return 1

    app_proc = None
    if port_busy(LOCAL_PORT):
        log(f"[2/3] {LOCAL_PORT} 端口已有实例在跑，直接复用")
    else:
        log(f"[2/3] 正在启动公网模式实例（端口 {LOCAL_PORT}）...")
        env = dict(os.environ, PORT=str(LOCAL_PORT))
        app_proc = subprocess.Popen(
            [sys.executable, str(BASE_DIR / "cloud_entry.py")],
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )

    if not wait_ready(LOCAL_PORT):
        log("[错误] 应用没能在 90 秒内就绪，请检查端口占用或数据库。")
        if app_proc:
            app_proc.terminate()
        return 1
    log("      应用已就绪")

    log("[3/3] 正在建立公网隧道...")
    tunnel = subprocess.Popen(
        [str(CLOUDFLARED), "tunnel", "--url", f"http://127.0.0.1:{LOCAL_PORT}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    public_url = None
    started = time.time()
    assert tunnel.stdout is not None
    for line in tunnel.stdout:
        if public_url is None:
            found = URL_RE.search(line)
            if found:
                public_url = found.group(0)
                log("")
                log("=" * 62)
                log("   手机浏览器打开下面这个地址（已复制到剪贴板）：")
                log("")
                log(f"   {public_url}")
                log("")
                log("   账号 admin1    密码 rx123456")
                log("   手机和电脑不用连同一个 WiFi，走公网就行")
                log("=" * 62)
                log("")
                log("   注意：关掉本窗口/电脑休眠后链接就失效，")
                log("        下次重新运行会得到一个新地址。")
                log("        按 Ctrl+C 停止。")
                log("")
                copy_to_clipboard(public_url)
                save_link(public_url)
        if time.time() - started > 120 and public_url is None:
            break

    if public_url is None:
        log("[错误] 隧道建立失败（没拿到公网地址）。")
        log("       常见原因：网络限制出站 443/7844 端口，或防火墙拦截。")
        tunnel.terminate()
        if app_proc:
            app_proc.terminate()
        return 1

    try:
        tunnel.wait()
    except KeyboardInterrupt:
        log("正在关闭...")
    finally:
        tunnel.terminate()
        if app_proc:
            app_proc.terminate()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
