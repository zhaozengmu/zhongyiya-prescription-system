"""初始化系统基础数据。

用法：python manage.py init_data

创建内容：
1. 七类角色（医师、药师、药师组长、药房管理员、医务科、管理员、患者）
2. 权限点与角色权限分配（含审核复审、发药、药房管理、医务科统计）
3. 审核规则库初始清单（含适用范围 scope）
4. 药材字典（含道地产地、炮制规格、功效主治、拼音首字母、库存与预警线）
5. 供应商与入库批次（含近效期/库存预警示例）
6. 经典方剂模板
7. 演示账号
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.prescriptions.models import (
    AuditRule,
    FormulaItem,
    FormulaTemplate,
    Herb,
    HerbBatch,
    Supplier,
)
from apps.users.models import Doctor, Permission, Pharmacist, Role, RolePermission, User, UserRole

# 权限编码清单
# 约定：这里只保留「代码里真的会校验」的权限点，避免出现定义了却从不校验的空壳权限。
PERMISSIONS = [
    ("患者:读", "患者读取", "action"),
    ("患者:新增", "患者建档", "action"),
    ("处方:新增", "新建处方", "action"),
    ("处方:修改", "修改处方", "action"),
    ("处方:提交", "提交处方", "action"),
    ("我的处方:读", "查看本人处方", "action"),
    ("审核:读", "查看审核", "action"),
    ("审核:领取", "领取审核任务", "action"),
    ("审核:通过", "初审通过", "action"),
    ("审核:复审", "复审通过", "action"),
    ("审核:退回", "审核退回", "action"),
    ("收费:确认", "缴费登记", "action"),
    ("发药:确认", "药房发药确认", "action"),
    ("药房:管理", "药材入库与库存管理", "action"),
    ("统计:读", "查看统计", "action"),
    ("医务科:统计", "医务科查看全量统计", "action"),
    ("日志:读", "查看日志", "action"),
    ("导出:执行", "导出数据", "action"),
    ("用户:读", "查看用户", "action"),
    ("字典:维护", "字典与规则维护", "action"),
]

# 角色权限分配
ROLE_PERMISSIONS = {
    "doctor": ["患者:读", "患者:新增", "处方:新增", "处方:修改", "处方:提交", "统计:读"],
    "pharmacist": ["审核:读", "审核:领取", "审核:通过", "审核:退回", "统计:读"],
    "pharmacist_lead": ["审核:读", "审核:领取", "审核:复审", "审核:退回", "统计:读"],
    "cashier": ["收费:确认", "统计:读"],
    "pharmacy": ["发药:确认", "药房:管理", "统计:读", "导出:执行"],
    "medical_affairs": ["医务科:统计", "统计:读", "日志:读", "导出:执行"],
    # 管理员负责系统维护与全局查看，不具备医师执业资格，因此不授予「处方:新增」
    "admin": [
        "用户:读", "患者:读", "统计:读", "日志:读", "导出:执行",
        "字典:维护", "审核:读", "发药:确认", "药房:管理", "收费:确认",
    ],
    # 患者只保留「看自己的处方」，不再拥有全量统计权限
    "patient": ["我的处方:读"],
}

# 医师演示账号的科室与职称
DOCTOR_PROFILES = {
    "doctor1": ("中医内科", "主任医师"),
    "doctor2": ("中医妇科", "副主任医师"),
    "doctor3": ("中医内科", "主治医师"),
    "doctor4": ("针灸推拿科", "主治医师"),
    "doctor5": ("中医儿科", "住院医师"),
}

# 演示患者档案：(姓名, 性别, 年龄, 体质, 孕产状态, 手机号, 既往病史, 就诊摘要)
DEMO_PATIENTS = [
    ("张三", "M", 35, "QI_DEFICIENCY", "NONE", "13800000000",
     "慢性胃炎 5 年", "反复乏力、纳差 3 月，辨证为脾胃气虚。"),
    ("林清和", "M", 42, "QI_DEFICIENCY", "NONE", "13910000001",
     "高血压（服药控制中）", "气短懒言、动则汗出，舌淡苔白，脉弱。"),
    ("苏念慈", "F", 29, "YIN_DEFICIENCY", "PREGNANT", "13910000002",
     "孕 16 周，无药物过敏史", "妊娠中期口干咽燥、夜寐不安，需避用妊娠禁忌药。"),
    ("顾长风", "M", 58, "PHLEGM_DAMP", "NONE", "13910000003",
     "2 型糖尿病 8 年，高脂血症", "形体肥胖、胸闷痰多，舌苔厚腻，脉滑。"),
    ("白露", "F", 34, "QI_STAGNATION", "NONE", "13910000004",
     "乳腺增生", "情志抑郁、胁肋胀痛，月经先后不定期。"),
    ("郝思远", "M", 8, "BALANCED", "NONE", "13910000005",
     "易感冒，无慢性病", "小儿反复咳嗽 2 周，痰少，需按儿童剂量折算。"),
    ("温玉汝", "F", 66, "YANG_DEFICIENCY", "NONE", "13910000006",
     "骨质疏松、甲状腺功能减退", "畏寒肢冷、腰膝酸软，冬季加重。"),
    ("邵明轩", "M", 51, "DAMP_HEAT", "NONE", "13910000007",
     "痛风病史 6 年", "口苦口黏、小便黄赤，舌红苔黄腻。"),
    ("傅小满", "F", 24, "BLOOD_STASIS", "NONE", "13910000008",
     "原发性痛经", "经行腹痛拒按、经血色暗有块，舌质紫暗。"),
    ("岑子谦", "M", 37, "BALANCED", "NONE", "13910000009",
     "无特殊病史，青霉素过敏", "偶发失眠多梦，工作压力大，舌脉基本正常。"),
]


# 审核规则库初始清单（含适用范围）
AUDIT_RULES = [
    # 十八反（强制拦截）
    ("R-18F-001", "18F", "甘草,甘遂", "block", "ALL", "十八反：甘草反甘遂，禁止同方", "《中国药典》2020年版一部"),
    ("R-18F-002", "18F", "甘草,大戟", "block", "ALL", "十八反：甘草反大戟，禁止同方", "《中国药典》2020年版一部"),
    ("R-18F-003", "18F", "乌头,半夏", "block", "ALL", "十八反：乌头反半夏，禁止同方", "《中国药典》2020年版一部"),
    ("R-18F-004", "18F", "乌头,瓜蒌", "block", "ALL", "十八反：乌头反瓜蒌，禁止同方", "《中国药典》2020年版一部"),
    ("R-18F-005", "18F", "乌头,贝母", "block", "ALL", "十八反：乌头反贝母，禁止同方", "《中国药典》2020年版一部"),
    ("R-18F-006", "18F", "藜芦,人参", "block", "ALL", "十八反：藜芦反人参，禁止同方", "《中国药典》2020年版一部"),
    # 十九畏（预警）
    ("R-19W-001", "19W", "硫黄,朴硝", "warn", "ALL", "十九畏：硫黄畏朴硝，请核对", "《中国药典》2020年版一部"),
    ("R-19W-002", "19W", "水银,砒霜", "warn", "ALL", "十九畏：水银畏砒霜，请核对", "《中国药典》2020年版一部"),
    ("R-19W-003", "19W", "人参,五灵脂", "warn", "ALL", "十九畏：人参畏五灵脂，请核对", "《中国药典》2020年版一部"),
    ("R-19W-004", "19W", "丁香,郁金", "warn", "ALL", "十九畏：丁香畏郁金，请核对", "《中国药典》2020年版一部"),
    # 妊娠禁忌
    ("R-PR-001", "PR", "麝香", "block", "ALL", "妊娠禁忌：麝香禁用", "《中国药典》2020年版一部"),
    ("R-PR-002", "PR", "三棱,莪术", "warn", "ALL", "妊娠慎用破血药", "《中国药典》2020年版一部"),
    ("R-PR-003", "PR", "桃仁", "warn", "ALL", "妊娠慎用：桃仁", "《中国药典》2020年版一部"),
    ("R-PR-004", "PR", "红花", "warn", "ALL", "妊娠慎用：红花", "《中国药典》2020年版一部"),
    # 毒性剂量上限
    ("R-TX-001", "TX", "附子", "block", "ALL", "附子超剂量上限", "《中国药典》2020年版一部"),
    ("R-TX-002", "TX", "细辛", "warn", "ALL", "细辛用量请复核", "《中国药典》2020年版一部"),
    ("R-TX-003", "TX", "制川乌", "block", "ALL", "制川乌超剂量上限", "《中国药典》2020年版一部"),
    ("R-TX-004", "TX", "制草乌", "block", "ALL", "制草乌超剂量上限", "《中国药典》2020年版一部"),
    # 用法用量规范
    ("R-YY-001", "YY", "", "warn", "ALL", "特殊煎法未标注（先煎/后下）", "《处方管理办法》"),
    # 特殊人群
    ("R-SP-001", "SP", "", "warn", "ALL", "特殊人群剂量需折算确认", "《中国药典》2020年版一部"),
]

# 药材字典：(名称, 拼音, 首字母, 类别, 性味, 功效, 产地, 炮制, 剂量上限, 是否毒性)
HERBS = [
    ("甘草", "gancao", "gc", "补气药", "甘平", "补脾益气，清热解毒，调和诸药", "内蒙古", "生甘草", 10, False),
    ("甘遂", "gansui", "gs", "泻下药", "苦寒有毒", "泻水逐饮，消肿散结", "山西", "醋甘遂", 3, True),
    ("大戟", "daji", "dj", "泻下药", "苦寒有毒", "泻水逐饮", "江苏", "醋大戟", 3, True),
    ("半夏", "banxia", "bx", "化痰药", "辛温有毒", "燥湿化痰，降逆止呕", "四川", "法半夏", 9, True),
    ("乌头", "wutou", "wt", "温里药", "辛热有毒", "祛风除湿，温经止痛", "四川", "制川乌", 3, True),
    ("人参", "renshen", "rs", "补气药", "甘温", "大补元气，复脉固脱", "吉林", "生晒参", 9, False),
    ("藜芦", "lilu", "ll", "涌吐药", "辛苦寒有毒", "涌吐风痰", "山西", "生藜芦", 3, True),
    ("瓜蒌", "gualou", "gl", "化痰药", "甘寒", "清热化痰，宽胸散结", "山东", "全瓜蒌", 12, False),
    ("贝母", "beimu", "bm", "化痰药", "苦寒", "清热化痰，润肺止咳", "浙江", "浙贝母", 9, False),
    ("硫黄", "liuhuang", "lh", "攻毒药", "酸温有毒", "外用解毒杀虫", "湖南", "制硫黄", 3, True),
    ("朴硝", "puxiao", "px", "泻下药", "咸寒", "泻热通便，润燥软坚", "青海", "芒硝", 10, False),
    ("五灵脂", "wulingzhi", "wlz", "活血药", "苦甘温", "活血止痛，化瘀止血", "河北", "醋五灵脂", 6, False),
    ("丁香", "dingxiang", "dx", "温里药", "辛温", "温中降逆，补肾助阳", "海南", "公丁香", 3, False),
    ("郁金", "yujin", "yj", "活血药", "辛苦寒", "活血止痛，行气解郁", "四川", "郁金", 9, False),
    ("麝香", "shexiang", "sx", "开窍药", "辛温", "开窍醒神，活血通经", "西藏", "麝香", 0.15, True),
    ("三棱", "sanleng", "sl", "活血药", "苦辛平", "破血行气，消积止痛", "江苏", "醋三棱", 9, False),
    ("莪术", "ezhu", "ez", "活血药", "辛苦温", "行气破血，消积止痛", "广西", "醋莪术", 9, False),
    ("桃仁", "taoren", "tr", "活血药", "苦平", "活血祛瘀，润肠通便", "河北", "桃仁", 9, False),
    ("红花", "honghua", "hh", "活血药", "辛温", "活血通经，散瘀止痛", "新疆", "红花", 6, False),
    ("附子", "fuzi", "fz", "温里药", "辛甘热有毒", "回阳救逆，补火助阳", "四川", "制附子", 9, True),
    ("细辛", "xixin", "xx", "解表药", "辛温", "祛风散寒，通窍止痛", "辽宁", "细辛", 3, True),
    ("制川乌", "zhichuanwu", "zcw", "祛风湿药", "辛苦热有毒", "祛风除湿，温经止痛", "四川", "制川乌", 3, True),
    ("制草乌", "zhicaowu", "zcwu", "祛风湿药", "辛苦热有毒", "祛风除湿，温经止痛", "四川", "制草乌", 3, True),
    ("黄芪", "huangqi", "hq", "补气药", "甘温", "补气固表，托毒生肌", "山西", "生黄芪", 15, False),
    ("当归", "danggui", "dg", "补血药", "甘辛温", "补血活血，调经止痛", "甘肃", "全当归", 12, False),
    ("白术", "baizhu", "bz", "补气药", "苦甘温", "健脾益气，燥湿利水", "浙江", "麸炒白术", 12, False),
    ("茯苓", "fuling", "fl", "利水药", "甘淡平", "利水渗湿，健脾宁心", "云南", "茯苓", 15, False),
    ("陈皮", "chenpi", "cp", "理气药", "辛苦温", "理气健脾，燥湿化痰", "广东", "陈皮", 9, False),
    ("白芍", "baishao", "bs", "补血药", "苦酸微寒", "养血柔肝，缓中止痛", "浙江", "炒白芍", 12, False),
    ("川芎", "chuanxiong", "cx", "活血药", "辛温", "活血行气，祛风止痛", "四川", "川芎", 9, False),
    ("熟地黄", "shudihuang", "sdh", "补血药", "甘微温", "滋阴补血，益精填髓", "河南", "熟地黄", 15, False),
    ("桂枝", "guizhi", "gz", "解表药", "辛甘温", "发汗解肌，温通经脉", "广西", "桂枝", 9, False),
    ("麻黄", "mahuang", "mh", "解表药", "辛苦温", "发汗解表，宣肺平喘", "山西", "生麻黄", 9, False),
    ("生姜", "shengjiang", "sj", "解表药", "辛温", "解表散寒，温中止呕", "山东", "生姜", 9, False),
    ("大枣", "dazao", "dz", "补气药", "甘温", "补中益气，养血安神", "河北", "大枣", 15, False),
    ("泽泻", "zexie", "zx", "利水药", "甘寒", "利水渗湿，泄热", "福建", "泽泻", 12, False),
    ("山药", "shanyao", "sy", "补气药", "甘平", "补脾养胃，生津益肺", "河南", "山药", 15, False),
    ("山茱萸", "shanzhuyu", "szy", "收涩药", "酸微温", "补益肝肾，收涩固脱", "浙江", "山茱萸", 12, False),
    ("牡丹皮", "mudanpi", "mdp", "清热药", "苦辛微寒", "清热凉血，活血化瘀", "安徽", "牡丹皮", 9, False),
    ("柴胡", "chaihu", "ch", "解表药", "苦辛微寒", "和解退热，疏肝解郁", "河北", "柴胡", 9, False),
    ("薄荷", "bohe", "bh", "解表药", "辛凉", "疏散风热，清利头目", "江苏", "薄荷", 6, False),
    ("防风", "fangfeng", "ff", "解表药", "辛甘微温", "祛风解表，胜湿止痛", "内蒙古", "防风", 9, False),
    ("杏仁", "xingren", "xr", "止咳平喘药", "苦微温", "降气止咳平喘，润肠通便", "河北", "苦杏仁", 9, False),
]

# 经典方剂模板
FORMULAS = [
    {"name": "四君子汤", "category": "补气剂", "indication": "脾胃气虚证",
     "items": [("人参", 9), ("白术", 9), ("茯苓", 9), ("甘草", 6)]},
    {"name": "四物汤", "category": "补血剂", "indication": "营血虚滞证",
     "items": [("当归", 9), ("川芎", 9), ("白芍", 9), ("熟地黄", 12)]},
    {"name": "二陈汤", "category": "祛痰剂", "indication": "湿痰证",
     "items": [("半夏", 9), ("陈皮", 9), ("茯苓", 9), ("甘草", 6)]},
    {"name": "补中益气汤", "category": "补气剂", "indication": "脾胃气虚、中气下陷",
     "items": [("黄芪", 15), ("人参", 9), ("白术", 9), ("当归", 9), ("陈皮", 6), ("甘草", 6)]},
    {"name": "桂枝汤", "category": "解表剂", "indication": "外感风寒表虚证",
     "items": [("桂枝", 9), ("白芍", 9), ("生姜", 9), ("大枣", 12), ("甘草", 6)]},
    {"name": "麻黄汤", "category": "解表剂", "indication": "外感风寒表实证",
     "items": [("麻黄", 9), ("桂枝", 6), ("杏仁", 9), ("甘草", 3)]},
    {"name": "六味地黄丸", "category": "补益剂", "indication": "肾阴亏损证",
     "items": [("熟地黄", 24), ("山茱萸", 12), ("山药", 12), ("泽泻", 9), ("牡丹皮", 9), ("茯苓", 9)]},
    {"name": "逍遥散", "category": "和解剂", "indication": "肝郁血虚脾弱证",
     "items": [("柴胡", 9), ("当归", 9), ("白芍", 9), ("白术", 9), ("茯苓", 9), ("甘草", 6), ("薄荷", 3), ("生姜", 3)]},
]

# 供应商
SUPPLIERS = [
    ("北京同仁堂饮片厂", "京20150001", "张经理", "010-88886666", "北京市东城区"),
    ("云南白药集团", "滇20160002", "李经理", "0871-66668888", "云南省昆明市"),
    ("康美药业股份有限公司", "粤20170003", "王经理", "020-88889999", "广东省广州市"),
]

# 入库批次示例 (药材名, 批次号, 供应商名, 数量kg, 有效天数偏移)
BATCHES = [
    ("甘草", "GC20261001", "北京同仁堂饮片厂", 60, 365),
    ("甘草", "GC20250901", "云南白药集团", 40, 30),      # 近效期（30天后到期）
    ("人参", "RS20261001", "北京同仁堂饮片厂", 20, 540),
    ("当归", "DG20260801", "康美药业股份有限公司", 50, 180),
    ("黄芪", "HQ20261101", "云南白药集团", 80, 400),
    ("附子", "FZ20260701", "康美药业股份有限公司", 15, 20),  # 近效期
]


class Command(BaseCommand):
    help = "初始化系统基础数据（角色、权限、规则库、字典、供应商批次、方剂模板、演示账号）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-demo",
            action="store_true",
            help="把演示账号密码重置为初始值并解除锁定（账号已存在时也生效）",
        )

    def handle(self, *args, **options):
        self.reset_demo = options.get("reset_demo", False)
        with transaction.atomic():
            self.create_roles_and_permissions()
            self.create_audit_rules()
            self.create_herbs_and_formulas()
            self.create_suppliers_and_batches()
            self.create_demo_accounts()
            self.create_demo_patients()
        self.stdout.write(self.style.SUCCESS("初始化完成！"))


    def create_roles_and_permissions(self):
        role_defs = [
            ("doctor", "医师", "接诊开方"),
            ("pharmacist", "药师", "处方初审"),
            ("pharmacist_lead", "药师组长", "处方复审"),
            ("cashier", "收费员", "处方缴费登记"),
            ("pharmacy", "药房管理员", "药材入库、库存与发药"),
            ("medical_affairs", "医务科", "查看全量统计报表"),
            ("admin", "系统管理员", "系统维护"),
            ("patient", "患者", "查询本人处方"),
        ]
        roles = {}
        for code, name, desc in role_defs:
            roles[code] = Role.objects.get_or_create(code=code, defaults={"name": name, "description": desc})[0]

        perm_objs = {}
        for code, name, level in PERMISSIONS:
            perm_objs[code] = Permission.objects.update_or_create(
                code=code, defaults={"name": name, "level": level}
            )[0]

        # 清理已下线的权限点，保证权限表与代码校验点一致（可重复执行）
        valid_codes = {code for code, _, _ in PERMISSIONS}
        stale = Permission.objects.exclude(code__in=valid_codes)
        if stale.exists():
            RolePermission.objects.filter(permission__in=stale).delete()
            stale.delete()

        # 重建角色-权限映射，避免历史脏数据残留
        for role_code, perm_codes in ROLE_PERMISSIONS.items():
            role = roles[role_code]
            wanted = [perm_objs[pc] for pc in perm_codes if pc in perm_objs]
            RolePermission.objects.filter(role=role).exclude(permission__in=wanted).delete()
            for perm in wanted:
                RolePermission.objects.get_or_create(role=role, permission=perm)

        self.stdout.write(self.style.SUCCESS(
            f"角色（{len(roles)}）与权限（{len(PERMISSIONS)}）创建完成"
        ))


    def create_audit_rules(self):
        for rule_no, rule_type, herbs, level, scope, message, basis in AUDIT_RULES:
            AuditRule.objects.get_or_create(
                rule_no=rule_no,
                defaults={
                    "rule_type": rule_type, "herbs": herbs, "level": level,
                    "scope": scope, "message": message, "basis": basis, "version": "V1.0",
                },
            )
        self.stdout.write(self.style.SUCCESS(f"审核规则库创建完成（{len(AUDIT_RULES)} 条）"))

    def create_herbs_and_formulas(self):
        for name, pinyin, initial, category, prop, efficacy, origin, processing, max_dose, is_toxic in HERBS:
            Herb.objects.get_or_create(
                name=name,
                defaults={
                    "pinyin": pinyin, "pinyin_initial": initial, "category": category,
                    "property": prop, "efficacy": efficacy, "origin": origin,
                    "processing": processing, "max_dose": max_dose,
                    "is_toxic": is_toxic, "default_dose": max_dose,
                    "stock": 50, "stock_warning": 10,
                },
            )
        # 制造一个库存预警示例：细辛库存低于预警线
        Herb.objects.filter(name="细辛").update(stock=5, stock_warning=10)

        for f in FORMULAS:
            formula, _ = FormulaTemplate.objects.get_or_create(
                name=f["name"],
                defaults={"category": f["category"], "indication": f["indication"]},
            )
            for herb_name, dose in f["items"]:
                herb = Herb.objects.get(name=herb_name)
                FormulaItem.objects.get_or_create(formula=formula, herb=herb, defaults={"dose": dose})

        self.stdout.write(self.style.SUCCESS(f"药材字典（{len(HERBS)} 味）与经典方剂（{len(FORMULAS)} 个）创建完成"))

    def create_suppliers_and_batches(self):
        suppliers = {}
        for name, license_no, contact, phone, address in SUPPLIERS:
            suppliers[name] = Supplier.objects.get_or_create(
                name=name,
                defaults={"license_no": license_no, "contact": contact, "phone": phone, "address": address},
            )[0]

        today = timezone.now().date()
        for herb_name, batch_no, supplier_name, quantity, days in BATCHES:
            herb = Herb.objects.get(name=herb_name)
            supplier = suppliers[supplier_name]
            HerbBatch.objects.get_or_create(
                herb=herb, batch_no=batch_no,
                defaults={
                    "supplier": supplier,
                    "inbound_at": timezone.now(),
                    "expire_at": today + timezone.timedelta(days=days),
                    "quantity": quantity,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"供应商（{len(SUPPLIERS)} 个）与入库批次（{len(BATCHES)} 条）创建完成"))

    def create_demo_accounts(self):
        """创建演示账号（仅用于课程实训，模拟数据）。"""
        accounts = [
            # (username, password, name, role_code, is_doctor, is_pharmacist)
            ("admin1", "rx123456", "系统管理员", "admin", False, False),
            ("doctor1", "rx123456", "陈志明", "doctor", True, False),
            ("doctor2", "rx123456", "王玉晨", "doctor", True, False),
            ("doctor3", "rx123456", "李慕白", "doctor", True, False),
            ("doctor4", "rx123456", "周睿谦", "doctor", True, False),
            ("doctor5", "rx123456", "苏文垣", "doctor", True, False),
            ("pharmacist1", "rx123456", "雷婧怡", "pharmacist", False, True),
            ("pharmacist2", "rx123456", "尹姣", "pharmacist_lead", False, True),
            ("cashier1", "rx123456", "收费窗口", "cashier", False, False),
            ("pharmacy1", "rx123456", "沈雯", "pharmacy", False, False),
            ("patient1", "rx123456", "张三", "patient", False, False),
        ]

        for username, password, name, role_code, is_doctor, is_pharmacist in accounts:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"first_name": name, "last_name": "", "is_staff": (role_code == "admin")},
            )
            # --reset-demo 或首次创建时统一重设密码，避免"改了密码就再也跑不回去"
            if created or self.reset_demo:
                user.set_password(password)
                user.failed_attempts = 0
                user.locked_until = None
                user.save()
            role = Role.objects.get(code=role_code)
            UserRole.objects.get_or_create(user=user, role=role)

            if is_doctor:
                dept, title = DOCTOR_PROFILES.get(username, ("中医科", "主治医师"))
                Doctor.objects.get_or_create(
                    user=user,
                    defaults={"license_no": f"ZY{user.id:06d}", "department": dept, "title": title},
                )
            if is_pharmacist:
                Pharmacist.objects.get_or_create(
                    user=user, defaults={"license_no": f"YS{user.id:06d}"}
                )

            # 患者账号绑定一份患者档案，才能查"我的处方"
            if role_code == "patient":
                self._link_patient_profile(user, name)

        self.stdout.write(self.style.SUCCESS("演示账号创建完成"))
        self.stdout.write(self.style.WARNING(
            "演示账号：admin1 / doctor1~doctor5 / pharmacist1 / pharmacist2(药师组长) / "
            "cashier1(收费) / pharmacy1(药房) / patient1，密码统一 rx123456"
        ))

    def create_demo_patients(self):
        """批量创建演示患者档案（敏感字段加密存储）。"""
        from apps.patients.models import Patient
        from apps.utils.crypto import encrypt, hash_value

        created_count = 0
        for idx, (name, gender, age, constitution, pregnancy, phone, history, summary) in enumerate(
            DEMO_PATIENTS, start=1
        ):
            patient_no = f"20260101{idx:04d}"
            _, created = Patient.objects.get_or_create(
                patient_no=patient_no,
                defaults={
                    "name": name,
                    "gender": gender,
                    "age": age,
                    "constitution": constitution,
                    "pregnancy_status": pregnancy,
                    "phone_encrypted": encrypt(phone),
                    "phone_hash": hash_value(phone),
                    "medical_history": history,
                    "visit_summary": summary,
                },
            )
            if created:
                created_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"演示患者档案创建完成（新增 {created_count} 条，共 {len(DEMO_PATIENTS)} 条）")
        )

    def _link_patient_profile(self, user, name):
        """确保患者账号关联到一条患者档案（缺失则创建）。"""
        from apps.patients.models import Patient
        from apps.utils.crypto import encrypt, hash_value

        if hasattr(user, "patient_profile"):
            return

        phone = "13800000000"
        patient, _ = Patient.objects.get_or_create(
            patient_no="202601010001",
            defaults={
                "name": name,
                "gender": "M",
                "age": 35,
                "phone_encrypted": encrypt(phone),
                "phone_hash": hash_value(phone),
                "constitution": "QI_DEFICIENCY",
                "visit_summary": "反复乏力、纳差 3 月，辨证为脾胃气虚。",
            },
        )
        patient.user = user
        patient.save(update_fields=["user"])

