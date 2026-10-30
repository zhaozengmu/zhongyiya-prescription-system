"""中药字典、方剂模板、审核规则与处方模型。

对应数据存储：
- D3 中药与方剂字典：Herb、FormulaTemplate、FormulaItem、AuditRule
- D4 处方与明细库：Prescription、PrescriptionItem
"""
import logging

from django.db import IntegrityError, models, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class Herb(models.Model):
    """药材目录（中药字典）。

    支持道地药材产地溯源、炮制规格、功效主治、库存与近效期管理，
    拼音首字母用于医师开方高频检索（如输入 GC 检索甘草）。

    单位约定（重要）：处方剂量 ``dose`` 单位为 **g**，
    库存 ``stock`` 与预警线 ``stock_warning`` 单位为 **kg**，
    两者换算比例为 1000，扣减库存时务必换算，否则会差 1000 倍。
    """

    name = models.CharField("药材名称", max_length=64, unique=True)
    pinyin = models.CharField("拼音", max_length=100, blank=True)
    pinyin_initial = models.CharField("拼音首字母", max_length=16, blank=True, db_index=True)
    category = models.CharField("类别", max_length=32, blank=True)
    property = models.CharField("性味归经", max_length=100, blank=True)
    efficacy = models.CharField("功效主治", max_length=200, blank=True)
    origin = models.CharField("道地产地", max_length=64, blank=True)
    processing = models.CharField("炮制规格", max_length=64, blank=True)
    max_dose = models.DecimalField("剂量上限(g)", max_digits=8, decimal_places=2, null=True, blank=True)
    is_toxic = models.BooleanField("是否毒性药材", default=False)
    default_dose = models.DecimalField("常用剂量(g)", max_digits=8, decimal_places=2, null=True, blank=True)
    unit = models.CharField("单位", max_length=8, default="g")
    stock = models.DecimalField("库存数量(kg)", max_digits=12, decimal_places=2, default=0)
    stock_warning = models.DecimalField("库存预警线(kg)", max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "药材目录"
        verbose_name_plural = "药材目录"

    def __str__(self):
        return self.name

    def is_stock_shortage(self):
        """库存是否低于预警线。"""
        return self.stock <= self.stock_warning


class Supplier(models.Model):
    """药材供应商（供货药企），用于溯源。"""

    name = models.CharField("供应商名称", max_length=128, unique=True)
    license_no = models.CharField("经营许可证号", max_length=64, blank=True)
    contact = models.CharField("联系人", max_length=64, blank=True)
    phone = models.CharField("联系电话", max_length=32, blank=True)
    address = models.CharField("地址", max_length=200, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "药材供应商"
        verbose_name_plural = "药材供应商"

    def __str__(self):
        return self.name


class HerbBatch(models.Model):
    """药材入库批次，支持批次管理与近效期预警。"""

    herb = models.ForeignKey(Herb, on_delete=models.CASCADE, related_name="batches", verbose_name="药材")
    batch_no = models.CharField("批次号", max_length=64)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="供应商")
    inbound_at = models.DateTimeField("入库时间", null=True, blank=True)
    expire_at = models.DateField("有效期至", null=True, blank=True)
    quantity = models.DecimalField("入库数量(kg)", max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "药材入库批次"
        verbose_name_plural = "药材入库批次"
        unique_together = ("herb", "batch_no")

    def __str__(self):
        return f"{self.herb.name} - {self.batch_no}"

    @property
    def is_near_expiry(self):
        """是否近效期（90 天内到期）。"""
        if not self.expire_at:
            return False
        days = (self.expire_at - timezone.now().date()).days
        return 0 <= days <= 90


class FormulaTemplate(models.Model):
    """方剂模板，支持一键引用。"""

    name = models.CharField("方剂名称", max_length=64, unique=True)
    category = models.CharField("类别", max_length=32, blank=True)
    indication = models.CharField("主治", max_length=200, blank=True)
    description = models.TextField("方解说明", blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "方剂模板"
        verbose_name_plural = "方剂模板"

    def __str__(self):
        return self.name


class FormulaItem(models.Model):
    """方剂模板明细（组成药味）。"""

    formula = models.ForeignKey(FormulaTemplate, on_delete=models.CASCADE, related_name="items", verbose_name="方剂")
    herb = models.ForeignKey(Herb, on_delete=models.CASCADE, verbose_name="药材")
    dose = models.DecimalField("剂量(g)", max_digits=8, decimal_places=2)
    unit = models.CharField("单位", max_length=8, default="g")

    class Meta:
        verbose_name = "方剂明细"
        verbose_name_plural = "方剂明细"
        unique_together = ("formula", "herb")

    def __str__(self):
        return f"{self.formula.name} - {self.herb.name}"


class AuditRule(models.Model):
    """审核规则（D3 规则库）。

    规则以数据形式存储，由管理员在线维护并记录版本，
    审核引擎按当前版本加载，实现规则与代码解耦。

    ``herbs`` 语义：
    - 18F（十八反）/19W（十九畏）：``A,B`` 表示 A 与 B 配伍禁忌；
      若需表达「A 反 B/C/D」，请拆成多条规则或写 ``A:B,C,D``，
      引擎按「首个药材 × 其余任一同组药材」判定。
    - PR（妊娠禁忌）：任一味命中即触发。
    - TX（毒性剂量上限）：逐味按 Herb.max_dose 校验。
    """

    RULE_TYPE_CHOICES = [
        ("18F", "十八反"),
        ("19W", "十九畏"),
        ("PR", "妊娠禁忌"),
        ("TX", "毒性剂量上限"),
        ("YY", "用法用量规范"),
        ("SP", "特殊人群"),
    ]
    LEVEL_CHOICES = [
        ("block", "强制拦截"),
        ("warn", "预警提示"),
    ]
    SCOPE_CHOICES = [
        ("ALL", "通用"),
        ("OUTPATIENT", "门诊"),
        ("INPATIENT", "住院"),
    ]

    rule_no = models.CharField("规则编号", max_length=32, unique=True)
    rule_type = models.CharField("规则类型", max_length=8, choices=RULE_TYPE_CHOICES)
    herbs = models.CharField("涉及药材（逗号分隔）", max_length=200, blank=True)
    level = models.CharField("触发等级", max_length=8, choices=LEVEL_CHOICES, default="warn")
    scope = models.CharField("适用范围", max_length=16, choices=SCOPE_CHOICES, default="ALL")
    message = models.CharField("提示文案", max_length=200)
    basis = models.CharField("引用依据（药典版本/规范文号）", max_length=200, blank=True)
    version = models.CharField("版本", max_length=16, default="V1.0")
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "审核规则"
        verbose_name_plural = "审核规则"

    def __str__(self):
        return f"{self.rule_no} {self.message}"


class Prescription(models.Model):
    """处方主记录（D4）。"""

    STATUS_CHOICES = [
        ("DRAFT", "草稿"),
        ("SUBMITTED", "待预审"),
        ("PRECHECKED", "已预审"),
        ("ALERTED", "预警拦截"),
        ("REVIEWING", "初审中"),
        ("FINAL_REVIEWING", "复审中"),
        ("APPROVED", "已通过待缴费"),
        ("REJECTED", "已驳回"),
        ("DISPENSED", "已发药完结"),
    ]
    TYPE_CHOICES = [
        ("OUTPATIENT", "门诊处方"),
        ("INPATIENT", "住院处方"),
        ("DECOCTION", "代煎处方"),
    ]
    PAY_STATUS_CHOICES = [
        ("UNPAID", "未缴费"),
        ("PAID", "已缴费"),
    ]

    presc_no = models.CharField("处方号", max_length=32, unique=True)
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.CASCADE, related_name="prescriptions", verbose_name="患者"
    )
    doctor = models.ForeignKey(
        "users.Doctor", on_delete=models.CASCADE, related_name="prescriptions", verbose_name="医师"
    )
    diagnosis = models.CharField("诊断", max_length=200, blank=True)
    syndrome = models.CharField("辨证结论", max_length=200, blank=True)
    presc_type = models.CharField("处方类型", max_length=16, choices=TYPE_CHOICES, default="OUTPATIENT")
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default="DRAFT", db_index=True)
    pay_status = models.CharField("缴费状态", max_length=16, choices=PAY_STATUS_CHOICES, default="UNPAID")
    paid_at = models.DateTimeField("缴费时间", null=True, blank=True)
    paid_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="paid_prescriptions", verbose_name="收费人",
    )
    # 代煎处方字段
    decoction_note = models.CharField("代煎备注", max_length=200, blank=True)
    pickup_time = models.DateTimeField("取药时间", null=True, blank=True)
    pickup_person = models.CharField("取药人", max_length=64, blank=True)
    # 打印/医嘱字段
    total_doses = models.IntegerField("剂数", default=1)
    dosage_note = models.CharField("服用医嘱", max_length=200, blank=True, default="遵医嘱，每日一剂，水煎分两次温服。")
    # 预审结果快照（JSON），避免每次打开审核页都重跑全量规则
    precheck_snapshot = models.TextField("预审结果快照", blank=True)
    version = models.IntegerField("版本号（乐观锁）", default=1)
    is_deleted = models.BooleanField("逻辑删除", default=False)
    created_at = models.DateTimeField("创建时间", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "处方"
        verbose_name_plural = "处方"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["doctor_id", "created_at"]),
            models.Index(fields=["patient_id", "created_at"]),
            models.Index(fields=["pay_status", "status"]),
        ]

    def __str__(self):
        return self.presc_no

    @property
    def is_paid(self):
        return self.pay_status == "PAID"

    @staticmethod
    def generate_presc_no():
        """按日期+流水生成处方号。"""
        now = timezone.now()
        prefix = now.strftime("%Y%m%d")
        last = (
            Prescription.objects.filter(presc_no__startswith=prefix)
            .order_by("-presc_no")
            .first()
        )
        seq = 1
        if last:
            try:
                seq = int(last.presc_no[-4:]) + 1
            except (ValueError, IndexError):
                seq = 1
        return f"P{prefix}{seq:04d}"

    @classmethod
    def create_with_generated_no(cls, max_retries=5, **kwargs):
        """带重试的创建：并发下处方号可能撞唯一约束，重试比直接 500 友好。"""
        last_error = None
        for _ in range(max_retries):
            try:
                with transaction.atomic():
                    return cls.objects.create(presc_no=cls.generate_presc_no(), **kwargs)
            except IntegrityError as exc:  # 并发撞号，重新生成
                last_error = exc
                logger.warning("处方号生成冲突，重试中")
        raise last_error


class PrescriptionItem(models.Model):
    """处方明细（药味）。"""

    prescription = models.ForeignKey(
        Prescription, on_delete=models.CASCADE, related_name="items", verbose_name="处方"
    )
    herb = models.ForeignKey(Herb, on_delete=models.CASCADE, verbose_name="药材")
    dose = models.DecimalField("剂量(g)", max_digits=8, decimal_places=2)
    unit = models.CharField("单位", max_length=8, default="g")
    decoction = models.CharField("煎服法", max_length=100, blank=True)
    usage = models.CharField("用法用量", max_length=100, blank=True)

    class Meta:
        verbose_name = "处方明细"
        verbose_name_plural = "处方明细"
        # 联合唯一索引：同一处方不能重复录入同一药材
        unique_together = ("prescription", "herb")
        indexes = [models.Index(fields=["herb", "prescription"])]

    def __str__(self):
        return f"{self.prescription.presc_no} - {self.herb.name}"
