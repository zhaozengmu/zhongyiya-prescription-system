"""用户、角色、权限、医师档案模型。

对应数据存储 D1（用户与权限库），支撑 RBAC 三级权限：
菜单权限、操作权限、接口权限。
"""
from django.contrib.auth.models import AbstractUser
from django.db import models

# 注册者身份类型：决定注册表单采集哪些资料、审核通过后授予什么角色
APPLY_ROLE_CHOICES = [
    ("patient", "患者"),
    ("doctor", "医师"),
    ("pharmacist", "药师"),
    ("cashier", "收费员"),
    ("pharmacy", "药房管理员"),
]

# 注册审核状态：医护类账号须审核通过后才能执业，患者注册即用
APPLY_STATUS_CHOICES = [
    ("NONE", "无需审核"),
    ("PENDING", "待审核"),
    ("APPROVED", "已通过"),
    ("REJECTED", "已驳回"),
]

GENDER_CHOICES = [("M", "男"), ("F", "女"), ("U", "保密")]


class User(AbstractUser):
    """系统用户，扩展中医业务字段与注册者档案。

    账号与医师/药师档案一一对应；角色通过多对多关联。
    注册时采集的身份、资质信息落在本表，医师/药师须管理员审核通过后
    才分配角色并建立执业档案（对应《医师法》执业资质准入要求）。
    """

    phone = models.CharField("联系电话", max_length=20, blank=True, null=True)
    # 登录失败锁定相关
    failed_attempts = models.IntegerField("连续失败次数", default=0)
    locked_until = models.DateTimeField("锁定截止时间", null=True, blank=True)

    # ===== 注册者基本信息 =====
    gender = models.CharField("性别", max_length=2, choices=GENDER_CHOICES, default="U")
    birth_date = models.DateField("出生日期", null=True, blank=True)
    id_card_encrypted = models.TextField("身份证号密文", blank=True)
    id_card_hash = models.CharField("身份证号哈希", max_length=64, blank=True, db_index=True)
    address = models.CharField("通讯地址", max_length=200, blank=True)
    emergency_contact = models.CharField("紧急联系人", max_length=64, blank=True)
    emergency_phone = models.CharField("紧急联系人电话", max_length=20, blank=True)

    # ===== 注册者身份与资质 =====
    apply_role = models.CharField("申请身份", max_length=16, choices=APPLY_ROLE_CHOICES, default="patient")
    apply_status = models.CharField(
        "审核状态", max_length=16, choices=APPLY_STATUS_CHOICES, default="NONE", db_index=True
    )
    license_no = models.CharField("执业证/资格证号", max_length=64, blank=True)
    department = models.CharField("科室/部门", max_length=64, blank=True)
    title = models.CharField("职称", max_length=32, blank=True)
    institution = models.CharField("工作单位/毕业院校", max_length=128, blank=True)
    practice_years = models.IntegerField("从业年限", null=True, blank=True)
    education = models.CharField("最高学历", max_length=32, blank=True)
    apply_note = models.CharField("申请说明", max_length=300, blank=True)

    # ===== 审核留痕 =====
    reviewed_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_applications", verbose_name="审核人",
    )
    reviewed_at = models.DateTimeField("审核时间", null=True, blank=True)
    review_note = models.CharField("审核意见", max_length=300, blank=True)

    class Meta:
        verbose_name = "用户"
        verbose_name_plural = "用户"

    def __str__(self):
        return f"{self.username}（{self.get_full_name()}）"

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def age(self):
        """由出生日期推算年龄，未填返回 None。"""
        if not self.birth_date:
            return None
        from django.utils import timezone

        today = timezone.localdate()
        return today.year - self.birth_date.year - (
            (today.month, today.day) < (self.birth_date.month, self.birth_date.day)
        )

    @property
    def is_staff_applicant(self):
        """是否为需要资质审核的医护/窗口岗位。"""
        return self.apply_role != "patient"

    @property
    def pending_review(self):
        return self.apply_status == "PENDING"


class Role(models.Model):
    """角色，如医师、药师、管理员、患者。"""

    code = models.CharField("角色编码", max_length=32, unique=True)
    name = models.CharField("角色名称", max_length=64)
    description = models.CharField("角色描述", max_length=200, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "角色"
        verbose_name_plural = "角色"

    def __str__(self):
        return self.name


class Permission(models.Model):
    """权限点，对应菜单/操作/接口三级资源。

    编码示例：患者:读、患者:新增、处方:提交、审核:通过 等。
    """

    LEVEL_CHOICES = [
        ("menu", "菜单权限"),
        ("action", "操作权限"),
        ("api", "接口权限"),
    ]
    code = models.CharField("权限编码", max_length=64, unique=True)
    name = models.CharField("权限名称", max_length=64)
    level = models.CharField("权限层级", max_length=16, choices=LEVEL_CHOICES, default="action")
    description = models.CharField("权限描述", max_length=200, blank=True)

    class Meta:
        verbose_name = "权限"
        verbose_name_plural = "权限"

    def __str__(self):
        return f"{self.name}({self.code})"


class RolePermission(models.Model):
    """角色与权限多对多关联。"""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, verbose_name="角色")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, verbose_name="权限")

    class Meta:
        verbose_name = "角色权限"
        verbose_name_plural = "角色权限"
        unique_together = ("role", "permission")

    def __str__(self):
        return f"{self.role} - {self.permission}"


class UserRole(models.Model):
    """用户与角色多对多关联。"""

    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="用户")
    role = models.ForeignKey(Role, on_delete=models.CASCADE, verbose_name="角色")

    class Meta:
        verbose_name = "用户角色"
        verbose_name_plural = "用户角色"
        unique_together = ("user", "role")

    def __str__(self):
        return f"{self.user} - {self.role}"


class Doctor(models.Model):
    """医师档案，与用户 1:1 关联。"""

    user = models.OneToOneField(User, on_delete=models.CASCADE, verbose_name="关联用户", related_name="doctor")
    license_no = models.CharField("执业证号", max_length=64, blank=True)
    department = models.CharField("科室", max_length=64, blank=True)
    title = models.CharField("职称", max_length=32, blank=True)
    clinic_hours = models.CharField("出诊时间", max_length=100, blank=True)
    signature_image = models.FileField("电子签名图片", upload_to="signatures/", blank=True, null=True)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "医师档案"
        verbose_name_plural = "医师档案"

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class Pharmacist(models.Model):
    """药师档案，与用户 1:1 关联。"""

    user = models.OneToOneField(User, on_delete=models.CASCADE, verbose_name="关联用户", related_name="pharmacist")
    license_no = models.CharField("执业证号", max_length=64, blank=True)
    is_active = models.BooleanField("是否启用", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "药师档案"
        verbose_name_plural = "药师档案"

    def __str__(self):
        return self.user.get_full_name() or self.user.username
