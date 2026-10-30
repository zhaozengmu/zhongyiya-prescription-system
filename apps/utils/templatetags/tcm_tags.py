"""模板工具标签：保留查询参数的分页链接、状态徽标映射。"""
from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    """在保留当前查询参数的前提下替换指定参数，用于分页链接。

    用法：<a href="?{% url_replace page=2 %}">第 2 页</a>
    """
    request = context.get("request")
    params = request.GET.copy() if request else {}
    for key, value in kwargs.items():
        params[key] = value
    return params.urlencode() if hasattr(params, "urlencode") else ""


@register.filter
def status_pill(status):
    """处方状态 -> pill 样式类。"""
    return {
        "DRAFT": "pill-gray",
        "SUBMITTED": "pill-blue",
        "PRECHECKED": "pill-blue",
        "ALERTED": "pill-red",
        "REVIEWING": "pill-amber",
        "FINAL_REVIEWING": "pill-amber",
        "APPROVED": "pill-wood",
        "REJECTED": "pill-red",
        "DISPENSED": "pill-green",
    }.get(status, "pill-gray")


@register.filter
def pay_pill(pay_status):
    """缴费状态 -> pill 样式类。"""
    return "pill-green" if pay_status == "PAID" else "pill-gray"


@register.filter
def pct(part, total):
    """计算百分比（整数），用于统计条。"""
    try:
        total = float(total)
        if not total:
            return 0
        return round(float(part) / total * 100)
    except (TypeError, ValueError):
        return 0
