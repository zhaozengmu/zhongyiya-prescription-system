from django.contrib import admin

from .models import OperationLog, StatDaily


@admin.register(OperationLog)
class OperationLogAdmin(admin.ModelAdmin):
    list_display = ("operator", "action_type", "object_type", "object_id", "result", "created_at")
    list_filter = ("action_type", "result", "created_at")
    search_fields = ("object_id", "detail")


@admin.register(StatDaily)
class StatDailyAdmin(admin.ModelAdmin):
    list_display = ("stat_date", "dimension", "dimension_key", "prescription_count", "item_count")
