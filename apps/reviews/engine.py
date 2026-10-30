"""处方审核规则引擎（P4 加工的核心）。

规则从 AuditRule 表加载（带缓存与版本），执行自动预审：
- 十八反类命中 -> 强制拦截（block）
- 十九畏类命中 -> 预警提示（warn）
- 妊娠禁忌 -> 结合患者孕产状态强制拦截或分级警示
- 毒性剂量上限 -> 超限强制拦截
- 用法用量规范 -> 缺项提醒
- 特殊人群 -> 折算建议

性能约束：一次预审只允许 **一次** 明细查询（select_related），
循环内不得再访问数据库，否则 20 味药 × N 条规则会产生几十次 SQL。
"""
import json
from collections import defaultdict

from django.core.cache import cache

CACHE_KEY = "audit_rules_active"
CACHE_TIMEOUT = 300


def _load_rules():
    """加载当前启用的审核规则（带缓存）。"""
    rules = cache.get(CACHE_KEY)
    if rules is None:
        from apps.prescriptions.models import AuditRule

        rules = list(
            AuditRule.objects.filter(is_active=True).values(
                "id", "rule_no", "rule_type", "herbs", "level", "scope", "message", "version", "basis"
            )
        )
        cache.set(CACHE_KEY, rules, timeout=CACHE_TIMEOUT)
    return rules


def invalidate_rules_cache():
    """规则变更后主动失效缓存。"""
    cache.delete(CACHE_KEY)


def _parse_herbs(raw):
    """解析 herbs 字段。

    返回 (mode, groups)：
    - ``"star"`` + (首味, [其余]) —— 支持 ``A:B,C,D`` 显式星形写法
    - ``"pair"`` + (味1, 味2)     —— ``A,B`` 两味互禁，必须同时出现
    - ``"star_auto"`` + (首味, [其余]) —— ``A,B,C`` 三味以上按星形处理
    """
    raw = (raw or "").strip()
    if not raw:
        return "none", ()

    if ":" in raw or "：" in raw:
        left, _, right = raw.replace("：", ":").partition(":")
        rights = [h.strip() for h in right.split(",") if h.strip()]
        return "star", (left.strip(), rights)

    parts = [h.strip() for h in raw.replace("，", ",").split(",") if h.strip()]
    if len(parts) <= 1:
        return "single", (parts[0] if parts else "", [])
    if len(parts) == 2:
        return "pair", (parts[0], parts[1])
    return "star_auto", (parts[0], parts[1:])


def _rule_herb_names(raw):
    """把任意写法的 herbs 字段摊平为「药材名列表」。

    注意 ``single`` 模式的 groups 是 ``(名称, [])``，直接 ``list(groups)``
    会把空列表当成药材名传下去，因此这里必须按 mode 分派后过滤假值。
    """
    mode, groups = _parse_herbs(raw)

    if mode == "none":
        return []
    if mode in ("star", "star_auto"):
        head, others = groups
        return [head, *others]
    return [g for g in groups if g]


def _match_combination(raw, herb_names):
    """判断配伍禁忌组合是否命中，返回命中的药材对（或 None）。

    两味规则要求同时出现；三味以上按「首味 × 其余任一味」判定，
    避免出现"把甘草反甘遂/大戟/海藻/芫花 写成一条规则后永远不触发"的漏判。
    """
    mode, groups = _parse_herbs(raw)

    if mode == "pair":
        a, b = groups
        if a in herb_names and b in herb_names:
            return f"{a}+{b}"
        return None

    if mode in ("star", "star_auto"):
        head, others = groups
        if head not in herb_names:
            return None
        hit = [o for o in others if o in herb_names]
        if hit:
            return f"{head}+{hit[0]}"
    return None


def precheck_prescription(prescription, items=None):
    """对处方执行自动预审。

    返回 dict：
    {
        "blocked": bool,          # 是否命中强制拦截规则
        "hits": [{rule_no, rule_type, level, message, herbs, version}],
        "result_text": str,       # 预审结论文本
        "suggestions": [str],     # 特殊人群折算建议
    }
    """
    if items is None:
        items = list(prescription.items.select_related("herb"))

    # 一次遍历建索引，后续全部内存计算
    herb_names = {item.herb.name for item in items}
    item_by_herb = {item.herb.name: item for item in items}

    patient = prescription.patient
    pregnancy = patient.pregnancy_status if patient else "NONE"

    # 按处方类型过滤规则适用范围：门诊 ALL+OUTPATIENT，住院 ALL+INPATIENT
    presc_scope = "INPATIENT" if prescription.presc_type == "INPATIENT" else "OUTPATIENT"
    rules = [r for r in _load_rules() if r.get("scope", "ALL") in ("ALL", presc_scope)]

    hits = []
    suggestions = []
    blocked = False

    for rule in rules:
        rule_type = rule["rule_type"]

        if rule_type in ("18F", "19W"):  # 十八反 / 十九畏：配伍组合
            pair = _match_combination(rule["herbs"], herb_names)
            if pair:
                hits.append(_format_hit(rule, extra=f"（命中：{pair}）"))
                if rule["level"] == "block":
                    blocked = True

        elif rule_type == "PR":  # 妊娠禁忌：任一味命中即触发
            if pregnancy in ("PREGNANT", "POSTPARTUM"):
                hit = [h for h in _rule_herb_names(rule["herbs"]) if h in herb_names]
                if hit:
                    hits.append(_format_hit(rule, extra=f"（{pregnancy_text(pregnancy)}，含 {','.join(hit)}）"))
                    if rule["level"] == "block":
                        blocked = True

        elif rule_type == "TX":  # 毒性剂量上限
            for name in _rule_herb_names(rule["herbs"]):
                item = item_by_herb.get(name)
                if not item or not item.herb.max_dose:
                    continue
                dose = float(item.dose)
                max_dose = float(item.herb.max_dose)
                if dose > max_dose:
                    hits.append(
                        _format_hit(rule, extra=f"（{name} 剂量 {dose:g}g 超上限 {max_dose:g}g）")
                    )
                    if rule["level"] == "block":
                        blocked = True

        elif rule_type == "YY":  # 用法用量规范：煎服法缺项
            missing = [item.herb.name for item in items if not item.decoction.strip()]
            if missing:
                shown = ",".join(missing[:5]) + ("…" if len(missing) > 5 else "")
                hits.append(_format_hit(rule, extra=f"（未标注煎服法：{shown}）"))

        elif rule_type == "SP":  # 特殊人群：给出实际折算建议
            if patient and patient.age is not None:
                factor = _dose_factor(patient.age)
                if factor < 1:
                    total = sum(float(i.dose) for i in items)
                    suggestions.append(
                        f"{patient.age} 岁患者建议按 {factor:g} 折算，全方总量约 {total * factor:.1f}g"
                    )
                    hits.append(_format_hit(rule, extra=f"（{patient.age} 岁，建议折算系数 {factor:g}）"))

    result_text = _build_result_text(hits)
    return {"blocked": blocked, "hits": hits, "result_text": result_text, "suggestions": suggestions}


def precheck_cached(prescription, items=None, refresh=False):
    """优先读取预审快照，避免审核页每次刷新都重跑规则。"""
    if not refresh and prescription.precheck_snapshot:
        try:
            return json.loads(prescription.precheck_snapshot)
        except (json.JSONDecodeError, TypeError):
            pass
    return precheck_prescription(prescription, items=items)


def save_precheck_snapshot(prescription, result=None):
    """把预审结果写入处方快照字段。"""
    result = result or precheck_prescription(prescription)
    prescription.precheck_snapshot = json.dumps(result, ensure_ascii=False)
    prescription.save(update_fields=["precheck_snapshot"])
    return result


def pregnancy_text(code):
    return {"PREGNANT": "孕期", "POSTPARTUM": "产后"}.get(code, code)


def _dose_factor(age):
    """按年龄给出剂量折算系数（参考儿童/老年用药通行折算）。"""
    if age is None:
        return 1.0
    if age < 1:
        return 0.25
    if age < 6:
        return 0.4
    if age < 12:
        return 0.6
    if age < 18:
        return 0.8
    if age > 70:
        return 0.75
    if age > 65:
        return 0.85
    return 1.0


def _format_hit(rule, extra=""):
    return {
        "rule_no": rule["rule_no"],
        "rule_type": rule["rule_type"],
        "level": rule["level"],
        "message": rule["message"] + extra,
        "herbs": rule["herbs"],
        "version": rule["version"],
        "basis": rule.get("basis", ""),
    }


def _build_result_text(hits):
    if not hits:
        return "预审通过，未命中配伍禁忌或剂量超限规则"
    blocked_hits = [h for h in hits if h["level"] == "block"]
    warn_hits = [h for h in hits if h["level"] == "warn"]
    parts = []
    if blocked_hits:
        parts.append(f"强制拦截 {len(blocked_hits)} 项")
    if warn_hits:
        parts.append(f"预警提示 {len(warn_hits)} 项")
    return "；".join(parts)


def hit_stats(hits):
    """按规则类型统计命中数量，供审核页展示。"""
    counter = defaultdict(int)
    for h in hits:
        counter[h["rule_type"]] += 1
    return dict(counter)
