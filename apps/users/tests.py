"""根路径与角色落地页的回归测试。

背景：``/`` 曾经没有任何路由，直接打开 8000 端口会撞上 Django 的 404 调试页，
用户看到 ``The empty path didn't match any of these``。这里守住三件事：
匿名访问必须先到登录页、各角色登录后落到有权访问的页面、落地页本身不能 403。
"""
from django.test import Client, TestCase

from apps.users.models import Permission, Role, RolePermission, User, UserRole


def grant(user, *perm_codes):
    role, _ = Role.objects.get_or_create(code=f"landing_{user.username}", defaults={"name": f"落地页角色-{user.username}"})
    UserRole.objects.get_or_create(user=user, role=role)
    for code in perm_codes:
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"name": code})
        RolePermission.objects.get_or_create(role=role, permission=perm)
    return user


class HomeRedirectTests(TestCase):
    def test_root_redirects_anonymous_to_login(self):
        """未登录访问根路径应跳登录页，而不是 404。"""
        r = Client().get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/login/")

    def test_root_redirects_admin_to_dashboard(self):
        grant(User.objects.create_user("landing_admin", password="pw123456"), "统计:读")
        c = Client()
        self.assertTrue(c.login(username="landing_admin", password="pw123456"))
        r = c.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/stats/dashboard/")

    def test_root_redirects_patient_to_own_prescriptions(self):
        """患者没有统计权限，不能被扔到驾驶舱撞 403。"""
        grant(User.objects.create_user("landing_patient", password="pw123456"), "我的处方:读")
        c = Client()
        self.assertTrue(c.login(username="landing_patient", password="pw123456"))
        r = c.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/prescriptions/mine/")

    def test_landing_page_is_reachable(self):
        """跳过去之后必须真的是 200，否则等于把 404 换成了 403。"""
        grant(User.objects.create_user("landing_pharmacy", password="pw123456"), "发药:确认")
        c = Client()
        self.assertTrue(c.login(username="landing_pharmacy", password="pw123456"))
        landing = c.get("/")["Location"]
        self.assertEqual(c.get(landing).status_code, 200)
