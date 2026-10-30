"""生成演示处方流水（仅用于课程演示与界面验收）。

覆盖状态机的全部关键节点：草稿、已提交、已预审、预警拦截、初审中、
复审中、待缴费、待发药、已发药、已驳回，并在近 14 天内分散创建时间，
使仪表盘的 7 日/14 日趋势图有真实形状。

用法：
    python manage.py seed_prescriptions
    python manage.py seed_prescriptions --reset   # 先清空演示处方再重建
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.prescriptions.models import FormulaTemplate, Prescription, PrescriptionItem
from apps.reviews.models import DispenseRecord, ReviewRecord
from apps.users.models import Doctor, Pharmacist, User

# (患者序号, 医师用户名, 方剂名, 处方类型, 目标状态, 缴费状态, 天数前)
DEMO_PLAN = [
    (1, "doctor1", "四君子汤", "OUTPATIENT", "DISPENSED", "PAID", 13),
    (2, "doctor1", "补中益气汤", "OUTPATIENT", "DISPENSED", "PAID", 12),
    (4, "doctor3", "二陈汤", "OUTPATIENT", "REJECTED", "UNPAID", 10),
    (5, "doctor2", "逍遥散", "OUTPATIENT", "APPROVED", "PAID", 8),
    (3, "doctor2", "四物汤", "OUTPATIENT", "APPROVED", "UNPAID", 7),
    (8, "doctor3", "二陈汤", "OUTPATIENT", "APPROVED", "UNPAID", 6),
    (6, "doctor5", "桂枝汤", "OUTPATIENT", "FINAL_REVIEWING", "UNPAID", 5),
    (7, "doctor1", "六味地黄丸", "OUTPATIENT", "REVIEWING", "UNPAID", 4),
    (9, "doctor2", "四物汤", "DECOCTION", "REVIEWING", "UNPAID", 3),
    (10, "doctor4", "桂枝汤", "OUTPATIENT", "PRECHECKED", "UNPAID", 2),
    (5, "doctor1", "四君子汤", "OUTPATIENT", "ALERTED", "UNPAID", 2),
    (2, "doctor3", "补中益气汤", "INPATIENT", "SUBMITTED", "UNPAID", 1),
    (4, "doctor2", "逍遥散", "OUTPATIENT", "SUBMITTED", "UNPAID", 1),
    (10, "doctor4", "六味地黄丸", "OUTPATIENT", "DRAFT", "UNPAID", 0),
]

DIAGNOSIS = {
    "四君子汤": ("慢性胃炎", "脾胃气虚证"),
    "补中益气汤": ("慢性疲劳综合征", "中气下陷证"),
    "二陈汤": ("慢性支气管炎", "湿痰证"),
    "四物汤": ("月经不调", "营血虚滞证"),
    "桂枝汤": ("上呼吸道感染", "外感风寒表虚证"),
    "六味地黄丸": ("更年期综合征", "肾阴亏损证"),
    "逍遥散": ("功能性消化不良", "肝郁血虚脾弱证"),
}

DECOCTION = "水煎温服，每日一剂分早晚两次"


class Command(BaseCommand):
    help = "生成演示处方流水（覆盖状态机各节点，供界面与报表验收）"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="先删除已有的演示处方再重建")

    def handle(self, *args, **options):
        if options.get("reset"):
            deleted, _ = Prescription.objects.filter(presc_no__startswith="P").delete()
            self.stdout.write(self.style.WARNING(f"已清理旧处方数据（{deleted} 行）"))

        with transaction.atomic():
            created = self._seed()

        self.stdout.write(self.style.SUCCESS(f"演示处方生成完成，新增 {created} 张"))

    def _seed(self):
        doctors = {d.user.username: d for d in Doctor.objects.select_related("user")}
        dispenser = User.objects.filter(username="pharmacy1").first()
        cashier = User.objects.filter(username="cashier1").first()
        initial_reviewer = Pharmacist.objects.filter(user__username="pharmacist1").first()
        lead_reviewer = Pharmacist.objects.filter(user__username="pharmacist2").first()

        created = 0
        for idx, (patient_seq, doctor_name, formula_name, presc_type, status, pay_status, days_ago) in enumerate(
            DEMO_PLAN, start=1
        ):
            doctor = doctors.get(doctor_name)
            if not doctor:
                continue
            formula = FormulaTemplate.objects.filter(name=formula_name).first()
            if not formula:
                continue

            patient_no = f"20260101{patient_seq:04d}"
            from apps.patients.models import Patient

            patient = Patient.objects.filter(patient_no=patient_no).first()
            if not patient:
                continue

            presc_no = f"P{timezone.now().strftime('%Y%m%d')}{idx:04d}"
            if Prescription.objects.filter(presc_no=presc_no).exists():
                continue

            diagnosis, syndrome = DIAGNOSIS.get(formula_name, ("中医内科杂病", "气血失调证"))
            presc = Prescription.objects.create(
                presc_no=presc_no,
                patient=patient,
                doctor=doctor,
                diagnosis=diagnosis,
                syndrome=syndrome,
                presc_type=presc_type,
                status=status,
                pay_status=pay_status,
                total_doses=7 if presc_type == "DECOCTION" else 5,
                decoction_note="代煎 7 袋，真空分装" if presc_type == "DECOCTION" else "",
            )

            PrescriptionItem.objects.bulk_create(
                [
                    PrescriptionItem(
                        prescription=presc,
                        herb=item.herb,
                        dose=item.dose,
                        unit=item.unit,
                        decoction=DECOCTION,
                    )
                    for item in formula.items.select_related("herb")
                ]
            )

            self._write_history(presc, status, pay_status, initial_reviewer, lead_reviewer, cashier, dispenser)

            # created_at 是 auto_now_add，只能建好后用 update 回填，才能形成趋势曲线
            created_at = timezone.now() - timedelta(days=days_ago, hours=idx % 7)
            Prescription.objects.filter(pk=presc.pk).update(created_at=created_at, updated_at=created_at)
            created += 1

        return created

    def _write_history(self, presc, status, pay_status, initial_reviewer, lead_reviewer, cashier, dispenser):
        """按目标状态补齐审核、缴费、发药流水，使时间线看起来连贯。"""
        precheck_text = "预审通过，未命中配伍禁忌或剂量超限规则"

        if status in ("REVIEWING", "FINAL_REVIEWING", "APPROVED", "DISPENSED", "REJECTED"):
            ReviewRecord.objects.create(
                prescription=presc,
                review_level="INITIAL",
                precheck_result=precheck_text,
                review_opinion="饮片配伍合理，剂量在安全区间" if status != "REJECTED" else "",
                reject_reason="方中炙甘草剂量偏高，建议下调至 6g 后重新提交" if status == "REJECTED" else "",
                pharmacist=initial_reviewer,
            )

        if status in ("FINAL_REVIEWING", "APPROVED", "DISPENSED"):
            ReviewRecord.objects.create(
                prescription=presc,
                review_level="FINAL",
                precheck_result=precheck_text,
                review_opinion="复核无误，同意调配",
                pharmacist=lead_reviewer,
            )

        if pay_status == "PAID":
            Prescription.objects.filter(pk=presc.pk).update(
                paid_at=timezone.now() - timedelta(days=1), paid_by=cashier
            )

        if status == "DISPENSED" and dispenser:
            DispenseRecord.objects.get_or_create(
                prescription=presc,
                defaults={"dispenser": dispenser, "checked_herbs": True, "note": "已核对饮片，患者签字确认"},
            )
