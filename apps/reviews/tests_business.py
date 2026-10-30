"""权限隔离与「缴费 → 发药」业务闭环的集成测试。

单独成文件而非并入 tests.py，是为了让 CI 能区分
「规则引擎/状态机单元测试」与「跨角色端到端流程测试」两类失败。
"""
import json

from django.test import Client, TestCase

from apps.patients.models import Patient
from apps.prescriptions.models import Herb, Prescription, PrescriptionItem
from apps.users.models import Doctor, Permission, Role, RolePermission, User, UserRole


def grant(user, *perm_codes):
    """给用户建一个临时角色并授予指定权限点。"""
    role, _ = Role.objects.get_or_create(code=f"tmp_{user.username}", defaults={"name": f"临时角色-{user.username}"})
    UserRole.objects.get_or_create(user=user, role=role)
    for code in perm_codes:
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"name": code})
        RolePermission.objects.get_or_create(role=role, permission=perm)
    return user


class PermissionIsolationTests(TestCase):
    """越权访问必须被拒绝，而不是返回一个空白的 200 页面。"""

    @classmethod
    def setUpTestData(cls):
        cls.patient_user = grant(User.objects.create_user("p_patient", password="pw123456"), "我的处方:读")
        cls.doctor_user = grant(
            User.objects.create_user("p_doctor", password="pw123456"),
            "处方:新增", "处方:提交", "患者:读",
        )
        cls.cashier_user = grant(User.objects.create_user("p_cashier", password="pw123456"), "收费:确认")
        cls.pharmacy_user = grant(User.objects.create_user("p_pharmacy", password="pw123456"), "发药:确认")

        cls.patient = Patient.objects.create(patient_no="ISO001", name="越权测试患者")
        cls.doctor = Doctor.objects.create(user=cls.doctor_user)
        cls.presc = Prescription.objects.create(
            presc_no="PISO0001", patient=cls.patient, doctor=cls.doctor, status="APPROVED",
        )
        PrescriptionItem.objects.create(prescription=cls.presc, herb=Herb.objects.create(name="当归"), dose=10)

    def _client(self, username):
        c = Client()
        self.assertTrue(c.login(username=username, password="pw123456"))
        return c

    def test_patient_cannot_list_all_prescriptions(self):
        """患者只能看自己的处方，不能进全量处方列表。"""
        r = self._client("p_patient").get("/prescriptions/")
        self.assertEqual(r.status_code, 403)

    def test_patient_can_view_own_prescriptions(self):
        r = self._client("p_patient").get("/prescriptions/mine/")
        self.assertEqual(r.status_code, 200)

    def test_doctor_cannot_enter_review_workbench(self):
        """开方与审方分离：医师不得进入审核台。"""
        r = self._client("p_doctor").get("/reviews/")
        self.assertEqual(r.status_code, 403)

    def test_cashier_cannot_enter_dispense_queue(self):
        """收费与发药分离：收费员不得进入发药队列。"""
        r = self._client("p_cashier").get("/prescriptions/dispense/")
        self.assertEqual(r.status_code, 403)

    def test_pharmacy_cannot_enter_payment_queue(self):
        r = self._client("p_pharmacy").get("/prescriptions/payment/")
        self.assertEqual(r.status_code, 403)


class PaymentDispenseFlowTests(TestCase):
    """APPROVED → 缴费(PAID) → 发药(DISPENSED)，并且发药必须扣减库存。"""

    @classmethod
    def setUpTestData(cls):
        cls.cashier = grant(User.objects.create_user("f_cashier", password="pw123456"), "收费:确认")
        cls.pharmacy = grant(User.objects.create_user("f_pharmacy", password="pw123456"), "发药:确认")
        cls.doctor_user = grant(User.objects.create_user("f_doctor", password="pw123456"), "处方:新增")
        cls.patient = Patient.objects.create(patient_no="FLOW01", name="闭环测试患者")
        cls.doctor = Doctor.objects.create(user=cls.doctor_user)
        cls.herb = Herb.objects.create(name="黄芪", stock=10, stock_warning=1, max_dose=30)

    def setUp(self):
        self.presc = Prescription.objects.create(
            presc_no=f"PFLOW{Prescription.objects.count() + 1:04d}",
            patient=self.patient,
            doctor=self.doctor,
            status="APPROVED",
            total_doses=5,
        )
        PrescriptionItem.objects.create(prescription=self.presc, herb=self.herb, dose=10, decoction="水煎温服")

    def _post(self, username, url, payload=None):
        c = Client()
        self.assertTrue(c.login(username=username, password="pw123456"))
        return c.post(url, data=json.dumps(payload or {}), content_type="application/json")

    def test_pay_then_dispense_deducts_stock(self):
        r = self._post("f_cashier", f"/api/v1/prescriptions/{self.presc.pk}/pay")
        self.assertEqual(r.status_code, 200, r.content)
        self.presc.refresh_from_db()
        self.assertEqual(self.presc.pay_status, "PAID")
        self.assertEqual(self.presc.paid_by, self.cashier)

        r = self._post("f_pharmacy", f"/api/v1/reviews/{self.presc.pk}/dispense", {"checked_herbs": True})
        self.assertEqual(r.status_code, 200, r.content)
        self.presc.refresh_from_db()
        self.herb.refresh_from_db()
        self.assertEqual(self.presc.status, "DISPENSED")
        # 10g × 5 剂 = 50g = 0.05kg
        self.assertAlmostEqual(float(self.herb.stock), 10 - 0.05, places=4)

    def test_cannot_dispense_before_payment(self):
        """未缴费不得发药——这是原来缺失的业务约束。"""
        r = self._post("f_pharmacy", f"/api/v1/reviews/{self.presc.pk}/dispense", {"checked_herbs": True})
        self.assertEqual(r.status_code, 409, r.content)  # 409 Conflict：状态不允许，而非请求格式错误
        self.presc.refresh_from_db()
        self.assertEqual(self.presc.status, "APPROVED")

    def test_cannot_pay_twice(self):
        self._post("f_cashier", f"/api/v1/prescriptions/{self.presc.pk}/pay")
        r = self._post("f_cashier", f"/api/v1/prescriptions/{self.presc.pk}/pay")
        self.assertEqual(r.status_code, 409, r.content)

    def test_cannot_dispense_twice(self):
        self._post("f_cashier", f"/api/v1/prescriptions/{self.presc.pk}/pay")
        self._post("f_pharmacy", f"/api/v1/reviews/{self.presc.pk}/dispense", {"checked_herbs": True})
        stock_after_first = Herb.objects.get(pk=self.herb.pk).stock
        r = self._post("f_pharmacy", f"/api/v1/reviews/{self.presc.pk}/dispense", {"checked_herbs": True})
        self.assertEqual(r.status_code, 409, r.content)
        # 重复发药不得二次扣库存
        self.assertEqual(Herb.objects.get(pk=self.herb.pk).stock, stock_after_first)

    def test_get_method_cannot_mutate(self):
        """变更类接口只接受 POST，GET 不得触发状态变更。"""
        c = Client()
        c.login(username="f_cashier", password="pw123456")
        r = c.get(f"/api/v1/prescriptions/{self.presc.pk}/pay")
        self.assertEqual(r.status_code, 405)
        self.presc.refresh_from_db()
        self.assertEqual(self.presc.pay_status, "UNPAID")
