from django.contrib import admin

from .models import DispenseRecord, Notification, ReviewRecord


@admin.register(ReviewRecord)
class ReviewRecordAdmin(admin.ModelAdmin):
    list_display = ("prescription", "review_level", "pharmacist", "created_at")
    list_filter = ("review_level", "created_at")


@admin.register(DispenseRecord)
class DispenseRecordAdmin(admin.ModelAdmin):
    list_display = ("prescription", "dispenser", "checked_herbs", "created_at")
    list_filter = ("checked_herbs", "created_at")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "title", "level", "is_read", "created_at")
    list_filter = ("level", "is_read")
