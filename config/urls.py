"""URL 路由总入口。

采用 MTV 模式的服务端渲染，所有业务接口统一以 /api/v1/ 为前缀，
为后续前后端分离预留扩展能力。
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.users.urls")),
    path("patients/", include("apps.patients.urls")),
    path("prescriptions/", include("apps.prescriptions.urls")),
    path("reviews/", include("apps.reviews.urls")),
    path("stats/", include("apps.stats.urls")),
    path("api/v1/", include("apps.users.api_urls")),
    path("api/v1/", include("apps.patients.api_urls")),
    path("api/v1/", include("apps.prescriptions.api_urls")),
    path("api/v1/", include("apps.reviews.api_urls")),
    path("api/v1/", include("apps.stats.api_urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
