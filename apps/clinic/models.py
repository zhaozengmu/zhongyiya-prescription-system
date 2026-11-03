"""门诊接诊、电子病历与病案服务申请模型。

对应高级医院门诊业务：
1. 挂号/预约（Registration）：线上预约、窗口挂号、医师加号，叫号接诊；
2. 电子病历（MedicalRecord）：中医四诊、体征、中医/西医诊断、治法医嘱；
3. 病案服务申请（MedicalRecordRequest）：病历复印、诊断证明、处方补打等
   《医疗机构病历管理规定》要求的患者服务事项，带受理与取件码闭环。
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Registration(models.Model):
    """门诊挂号记录（号源）。"""

    STATUS_CHOICES = [
        ("WAITING", "待就诊"),
        ("IN_PROGRESS", "就诊中"),
        ("FINISHED", "已完成"),
        ("CANCELLED", "已取消"),
        ("NOSHOW", "爽约"),
    ]
    TYPE_CHOICES = [
        ("NORMAL", "普通门诊"),
        ("EXPERT", "专家门诊"),
        ("FOLLOWUP", "复诊"),
        ("EMERGENCY", "急诊"),
    ]
    SOURCE_CHOICES = [
        ("ONLINE", "线上预约"),
        ("WINDOW", "窗口挂号"),
        ("DOCTOR", "医师加号"),
    ]
    FEE_MAP = {"NORMAL": 20, "EXPERT": 50, "FOLLOWUP": 15, "EMERGENCY": 30}

    reg_no = models.CharField("挂号单号", max_length=32, unique=True)
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.CASCADE, related_name="registrations", verbose_name="患者"
    )
    doctor = models.ForeignKey(
        "users.Doctor", on_delete=models.CASCADE, related_name="registrations", verbose_name="接诊医师"
    )
    visit_date = models.DateField("就诊日期", db_index=True)
    time_slot = models.CharField("就诊时段", max_length=32, default="上午 08:00-12:00")
    reg_type = models.CharField("号别", max_length=16, choices=TYPE_CHOICES, default="NORMAL")
    source = models.CharField("挂号来源", max_length=16, choices=SOURCE_CHOICES, default="ONLINE")
    fee = models.DecimalField("挂号费(元)", max_digits=8, decimal_places=2, default=20)
    pay_status = models.CharField(
        "缴费状态", max_length=16,
        choices=[("UNPAID", "未缴费"), ("PAID", "已缴费"), ("REFUND", "已退费")],
        default="UNPAID",
    )
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default="WAITING", db_index=True)
    queue_no = models.IntegerField("排队序号", default=1)
    chief_complaint = models.CharField("就诊原因", max_length=200, blank=True)
    remark = models.CharField("备注", max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_registrations", verbose_name="挂号操作人",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    called_at = models.DateTimeField("叫号时间", null=True, blank=True)
    finished_at = models.DateTimeField("完成时间", null=True, blank=True)

    class Meta:
        verbose_name = "门诊挂号"
        verbose_name_plural = "门诊挂号"
        ordering = ["visit_date", "queue_no"]
        indexes = [
            models.Index(fields=["doctor", "visit_date", "status"]),
            models.Index(fields=["patient", "visit_date"]),
        ]

    def __str__(self):
        return f"{self.reg_no} {self.patient.name}"

    @staticmethod
    def generate_reg_no() -> str:
        prefix = "GH" + timezone.localtime().strftime("%Y%m%d")
        last = Registration.objects.filter(reg_no__startswith=prefix).order_by("-reg_no").first()
        seq = 1
        if last:
            try:
                seq = int(last.reg_no[-4:]) + 1
            except (ValueError, IndexError):
                seq = 1
        return f"{prefix}{seq:04d}"

    def next_queue_no(self) -> int:
        """同一医师同一日期顺延取号。"""
        last = (
            Registration.objects.filter(doctor=self.doctor, visit_date=self.visit_date)
            .order_by("-queue_no")
            .first()
        )
        return (last.queue_no + 1) if last else 1

    @property
    def fee_of_type(self):
        return self.FEE_MAP.get(self.reg_type, 20)


class MedicalRecord(models.Model):
    """门诊电子病历（EMR），采用中医四诊 + 中西医双诊断结构。"""

    STATUS_CHOICES = [("DRAFT", "草稿"), ("SIGNED", "已签发")]

    record_no = models.CharField("病历号", max_length=32, unique=True)
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.CASCADE, related_name="medical_records", verbose_name="患者"
    )
    doctor = models.ForeignKey(
        "users.Doctor", on_delete=models.PROTECT, related_name="medical_records", verbose_name="接诊医师"
    )
    registration = models.OneToOneField(
        Registration, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="medical_record", verbose_name="关联挂号",
    )
    # 主诉与病史
    chief_complaint = models.CharField("主诉", max_length=200)
    present_illness = models.TextField("现病史", blank=True)
    past_history = models.TextField("既往史", blank=True)
    allergy_history = models.CharField("过敏史", max_length=200, blank=True)
    family_history = models.CharField("家族史", max_length=200, blank=True)
    # 中医四诊
    inspection = models.CharField("望诊", max_length=200, blank=True)
    auscultation = models.CharField("闻诊", max_length=200, blank=True)
    inquiry = models.CharField("问诊", max_length=200, blank=True)
    palpation = models.CharField("切诊（脉象）", max_length=200, blank=True)
    # 生命体征
    temperature = models.DecimalField("体温(℃)", max_digits=4, decimal_places=1, null=True, blank=True)
    pulse = models.IntegerField("脉搏(次/分)", null=True, blank=True)
    respiration = models.IntegerField("呼吸(次/分)", null=True, blank=True)
    blood_pressure = models.CharField("血压(mmHg)", max_length=20, blank=True)
    # 诊断与治法
    tcm_diagnosis = models.CharField("中医诊断", max_length=200, blank=True)
    syndrome = models.CharField("中医证型", max_length=200, blank=True)
    western_diagnosis = models.CharField("西医诊断", max_length=200, blank=True)
    treatment = models.CharField("治法", max_length=200, blank=True)
    advice = models.TextField("医嘱与调护", blank=True)
    status = models.CharField("病历状态", max_length=16, choices=STATUS_CHOICES, default="DRAFT", db_index=True)
    prescription = models.OneToOneField(
        "prescriptions.Prescription", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="medical_record", verbose_name="关联处方",
    )
    signed_at = models.DateTimeField("签发时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "电子病历"
        verbose_name_plural = "电子病历"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["patient", "created_at"])]

    def __str__(self):
        return f"{self.record_no} {self.patient.name}"

    @staticmethod
    def generate_record_no() -> str:
        prefix = "BL" + timezone.localtime().strftime("%Y%m%d")
        last = MedicalRecord.objects.filter(record_no__startswith=prefix).order_by("-record_no").first()
        seq = 1
        if last:
            try:
                seq = int(last.record_no[-4:]) + 1
            except (ValueError, IndexError):
                seq = 1
        return f"{prefix}{seq:04d}"

    @property
    def is_signed(self):
        return self.status == "SIGNED"


class MedicalRecordRequest(models.Model):
    """病案服务申请：病历复印、诊断证明、处方补打等。"""

    TYPE_CHOICES = [
        ("COPY", "病历复印件"),
        ("DIAGNOSIS", "诊断证明书"),
        ("PRESC", "处方补打"),
        ("SUMMARY", "门诊小结"),
        ("OTHER", "其他材料"),
    ]
    RELATION_CHOICES = [("SELF", "本人办理"), ("FAMILY", "亲属代办"), ("AGENT", "委托代办")]
    DELIVERY_CHOICES = [("PICKUP", "到院自取"), ("MAIL", "快递邮寄")]
    STATUS_CHOICES = [
        ("PENDING", "待受理"),
        ("ACCEPTED", "已受理"),
        ("COMPLETED", "已完成"),
        ("REJECTED", "已驳回"),
    ]

    request_no = models.CharField("申请单号", max_length=32, unique=True)
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.CASCADE, related_name="record_requests", verbose_name="患者"
    )
    applicant_name = models.CharField("申请人姓名", max_length=64)
    relation = models.CharField("与患者关系", max_length=16, choices=RELATION_CHOICES, default="SELF")
    phone = models.CharField("联系电话", max_length=20)
    id_card_last4 = models.CharField("证件后四位", max_length=8, blank=True)
    request_type = models.CharField("申请类型", max_length=16, choices=TYPE_CHOICES, default="COPY")
    purpose = models.CharField("用途说明", max_length=200)
    copies = models.IntegerField("份数", default=1)
    delivery = models.CharField("取件方式", max_length=16, choices=DELIVERY_CHOICES, default="PICKUP")
    address = models.CharField("邮寄地址", max_length=200, blank=True)
    status = models.CharField("受理状态", max_length=16, choices=STATUS_CHOICES, default="PENDING", db_index=True)
    handler = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="handled_record_requests", verbose_name="受理人",
    )
    handle_note = models.CharField("受理意见", max_length=300, blank=True)
    pickup_code = models.CharField("取件码", max_length=16, blank=True)
    accepted_at = models.DateTimeField("受理时间", null=True, blank=True)
    completed_at = models.DateTimeField("完成时间", null=True, blank=True)
    created_at = models.DateTimeField("申请时间", auto_now_add=True)

    class Meta:
        verbose_name = "病案服务申请"
        verbose_name_plural = "病案服务申请"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return f"{self.request_no} {self.patient.name}"

    @staticmethod
    def generate_request_no() -> str:
        prefix = "BA" + timezone.localtime().strftime("%Y%m%d")
        last = MedicalRecordRequest.objects.filter(request_no__startswith=prefix).order_by("-request_no").first()
        seq = 1
        if last:
            try:
                seq = int(last.request_no[-4:]) + 1
            except (ValueError, IndexError):
                seq = 1
        return f"{prefix}{seq:04d}"

    @property
    def status_tone(self):
        return {
            "PENDING": "pill-amber",
            "ACCEPTED": "pill-blue",
            "COMPLETED": "pill-green",
            "REJECTED": "pill-red",
        }.get(self.status, "pill-gray")
