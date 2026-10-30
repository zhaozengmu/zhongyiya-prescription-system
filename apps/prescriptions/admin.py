from django.contrib import admin

from .models import (
    AuditRule,
    FormulaItem,
    FormulaTemplate,
    Herb,
    HerbBatch,
    Prescription,
    PrescriptionItem,
    Supplier,
)


class FormulaItemInline(admin.TabularInline):
    model = FormulaItem
    extra = 1


@admin.register(Herb)
class HerbAdmin(admin.ModelAdmin):
    list_display = ("name", "pinyin_initial", "category", "origin", "processing",
                    "max_dose", "is_toxic", "stock", "stock_warning")
    search_fields = ("name", "pinyin", "pinyin_initial", "origin")
    list_filter = ("is_toxic", "category")


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "license_no", "contact", "phone")
    search_fields = ("name", "license_no")


@admin.register(HerbBatch)
class HerbBatchAdmin(admin.ModelAdmin):
    list_display = ("herb", "batch_no", "supplier", "inbound_at", "expire_at", "quantity")
    list_filter = ("supplier",)
    search_fields = ("herb__name", "batch_no")


@admin.register(FormulaTemplate)
class FormulaTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "indication")
    inlines = [FormulaItemInline]


@admin.register(AuditRule)
class AuditRuleAdmin(admin.ModelAdmin):
    list_display = ("rule_no", "rule_type", "herbs", "level", "scope", "message", "version", "is_active")
    list_filter = ("rule_type", "level", "scope", "is_active")


class PrescriptionItemInline(admin.TabularInline):
    model = PrescriptionItem
    extra = 0


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ("presc_no", "patient", "doctor", "presc_type", "status", "pay_status", "version", "created_at")
    list_filter = ("status", "presc_type", "pay_status")
    search_fields = ("presc_no", "patient__name")
    inlines = [PrescriptionItemInline]
