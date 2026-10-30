from django.urls import path

from . import views

app_name = "prescriptions"

urlpatterns = [
    path("", views.prescription_list, name="prescription_list"),
    path("create/", views.prescription_create, name="prescription_create"),
    path("mine/", views.my_prescriptions, name="my_prescriptions"),
    path("payment/", views.payment_queue, name="payment_queue"),
    path("dispense/", views.dispense_queue, name="dispense_queue"),
    path("<int:pk>/", views.prescription_detail, name="prescription_detail"),
    path("<int:pk>/print/", views.prescription_print, name="prescription_print"),
    path("<int:pk>/pdf/", views.prescription_pdf, name="prescription_pdf"),
    path("herbs/", views.herb_list, name="herb_list"),
    path("herbs/import/", views.herb_import, name="herb_import"),
    path("herbs/<int:pk>/card/", views.herb_card, name="herb_card"),
    path("herbs/<int:pk>/card/pdf/", views.herb_card_pdf, name="herb_card_pdf"),
    path("herbs/<int:pk>/stock/", views.herb_stock_adjust, name="herb_stock_adjust"),
]
