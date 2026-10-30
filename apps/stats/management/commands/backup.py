"""一键备份数据库。

用法：
    python manage.py backup            # 备份到 backup/ 目录
    python manage.py backup --output /path/to/dir
"""
import datetime
import os
import shutil

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "一键备份数据库（SQLite 直接复制文件，MySQL 用 mysqldump）"

    def add_arguments(self, parser):
        parser.add_argument("--output", type=str, default="", help="备份输出目录（默认项目 backup/ 目录）")

    def handle(self, *args, **options):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = options["output"] or os.path.join(settings.BASE_DIR, "backup")
        os.makedirs(out_dir, exist_ok=True)

        db_engine = settings.DATABASES["default"]["ENGINE"]

        if "sqlite" in db_engine:
            db_name = settings.DATABASES["default"]["NAME"]
            src = db_name
            if not os.path.isabs(src):
                src = os.path.join(settings.BASE_DIR, src)
            if not os.path.exists(src):
                self.stderr.write(self.style.ERROR(f"找不到 SQLite 数据库文件：{src}"))
                return
            dest = os.path.join(out_dir, f"backup_{ts}.sqlite3")
            shutil.copy2(src, dest)
            self.stdout.write(self.style.SUCCESS(f"SQLite 数据库已备份到：{dest}"))
            self.stdout.write(self.style.SUCCESS(f"备份大小：{os.path.getsize(dest) / 1024:.1f} KB"))
        elif "mysql" in db_engine:
            name = settings.DATABASES["default"]["NAME"]
            user = settings.DATABASES["default"].get("USER", "root")
            password = settings.DATABASES["default"].get("PASSWORD", "")
            host = settings.DATABASES["default"].get("HOST", "127.0.0.1")
            port = settings.DATABASES["default"].get("PORT", "3306")
            dest = os.path.join(out_dir, f"backup_{ts}.sql")
            cmd = (
                f'mysqldump -h {host} -P {port} -u {user} '
                f'--password={password} --single-transaction {name} > "{dest}"'
            )
            ret = os.system(cmd)
            if ret != 0:
                self.stderr.write(self.style.ERROR("mysqldump 执行失败，请确认已安装 MySQL 客户端工具"))
                return
            self.stdout.write(self.style.SUCCESS(f"MySQL 数据库已备份到：{dest}"))
        else:
            self.stderr.write(self.style.ERROR(f"不支持的数据库引擎：{db_engine}"))
