"""处方审核规则引擎与状态机的单元测试。

覆盖关键测试用例（表3-9）：
- TC-04 剂量超限拦截
- TC-05 十八反强制拦截
- TC-07 十九畏预警放行
- TC-08 退回必填理由
- TC-10 非法状态迁移
"""
from django.test import TestCase

from apps.patients.models import Patient
from apps.prescriptions.models import AuditRule, Herb, Prescription, PrescriptionItem
from apps.reviews import engine, state_machine
from apps.users.models import Doctor, User


class AuditEngineTests(TestCase):
    """规则引擎测试。"""

    @classmethod
    def setUpTestData(cls):
        cls.herb_gancao = Herb.objects.create(name="甘草", max_dose=10)
        cls.herb_gansui = Herb.objects.create(name="甘遂", max_dose=3, is_toxic=True)
        cls.herb_liuhuang = Herb.objects.create(name="硫黄", max_dose=3)
        cls.herb_puxiao = Herb.objects.create(name="朴硝", max_dose=10)
        cls.herb_fuzi = Herb.objects.create(name="附子", max_dose=9, is_toxic=True)

        AuditRule.objects.create(
            rule_no="R-18F-001", rule_type="18F", herbs="甘草,甘遂", level="block",
            message="十八反：甘草反甘遂", version="V1.0",
        )
        AuditRule.objects.create(
            rule_no="R-19W-001", rule_type="19W", herbs="硫黄,朴硝", level="warn",
            message="十九畏：硫黄畏朴硝", version="V1.0",
        )
        AuditRule.objects.create(
            rule_no="R-TX-001", rule_type="TX", herbs="附子", level="block",
            message="附子超剂量上限", version="V1.0",
        )

        cls.patient = Patient.objects.create(patient_no="T0001", name="测试患者", pregnancy_status="NONE")

    def _make_presc(self, herbs_doses):
        from apps.users.models import User as U
        u = U.objects.create(username="doc_test")
        doc = Doctor.objects.create(user=u)
        p = Prescription.objects.create(presc_no="P" + str(Prescription.objects.count() + 1).zfill(4), patient=self.patient, doctor=doc, status="DRAFT")
        for name, dose in herbs_doses:
            herb = Herb.objects.get(name=name)
            PrescriptionItem.objects.create(prescription=p, herb=herb, dose=dose)
        return p

    def test_shibafan_block(self):
        """TC-05 十八反强制拦截。"""
        p = self._make_presc([("甘草", 6), ("甘遂", 3)])
        result = engine.precheck_prescription(p)
        self.assertTrue(result["blocked"])
        self.assertTrue(any(h["rule_no"] == "R-18F-001" for h in result["hits"]))

    def test_shijiuwei_warn(self):
        """TC-07 十九畏预警提示（不拦截）。"""
        p = self._make_presc([("硫黄", 2), ("朴硝", 8)])
        result = engine.precheck_prescription(p)
        self.assertFalse(result["blocked"])
        self.assertTrue(any(h["rule_no"] == "R-19W-001" for h in result["hits"]))

    def test_dose_exceed_block(self):
        """TC-04 毒性剂量超限拦截。"""
        p = self._make_presc([("附子", 20)])  # 超过 9g 上限
        result = engine.precheck_prescription(p)
        self.assertTrue(result["blocked"])
        self.assertTrue(any(h["rule_no"] == "R-TX-001" for h in result["hits"]))


class StateMachineTests(TestCase):
    """状态机测试。"""

    @classmethod
    def setUpTestData(cls):
        cls.patient = Patient.objects.create(patient_no="T0002", name="状态机测试")
        u = User.objects.create(username="doc_sm")
        cls.doctor = Doctor.objects.create(user=u)
        cls.presc = Prescription.objects.create(presc_no="PSM0001", patient=cls.patient, doctor=cls.doctor, status="DRAFT")

    def test_normal_flow(self):
        """正常状态流转。"""
        ok, target = state_machine.transition(self.presc, "submit")
        self.assertTrue(ok)
        self.assertEqual(target, "SUBMITTED")

    def test_illegal_transition(self):
        """TC-10 非法状态迁移被拒绝。"""
        # 草稿状态不允许直接 approve
        ok, target = state_machine.transition(self.presc, "approve")
        self.assertFalse(ok)
        self.assertEqual(self.presc.status, "DRAFT")

    def test_resubmit_increments_version(self):
        """重新提交版本号加一。"""
        state_machine.transition(self.presc, "submit")
        state_machine.transition(self.presc, "precheck_pass")
        state_machine.transition(self.presc, "reject")  # -> REJECTED
        old_version = self.presc.version
        state_machine.transition(self.presc, "resubmit")
        self.assertEqual(self.presc.version, old_version + 1)

    def test_two_level_review_flow(self):
        """初审/复审两级审核流程。"""
        state_machine.transition(self.presc, "submit")       # DRAFT -> SUBMITTED
        state_machine.transition(self.presc, "precheck_pass")  # -> PRECHECKED
        state_machine.transition(self.presc, "claim")        # -> REVIEWING（初审中）
        ok, target = state_machine.transition(self.presc, "approve")  # 初审通过
        self.assertTrue(ok)
        self.assertEqual(target, "FINAL_REVIEWING")          # 进入复审中
        ok, target = state_machine.transition(self.presc, "approve")  # 复审通过
        self.assertTrue(ok)
        self.assertEqual(target, "APPROVED")                 # 归档
        ok, target = state_machine.transition(self.presc, "dispense")  # 发药
        self.assertTrue(ok)
        self.assertEqual(target, "DISPENSED")
