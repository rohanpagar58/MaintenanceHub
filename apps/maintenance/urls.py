from django.urls import path
from . import views

app_name = 'maintenance'

urlpatterns = [
    path('receipt/new/', views.create_receipt_page, name='create_receipt'),
    path('receipt/save/', views.save_receipt_view, name='save_receipt'),
    path('receipt/history/<str:member_id>/', views.member_receipt_history_view, name='member_receipt_history'),
]
