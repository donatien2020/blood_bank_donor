# Save this file exactly as: inventory/urls.py
from django.urls import path
from .views import BloodRequestListView, IssueUnitsView, process_issuance

urlpatterns = [
    path('inventory/requests/', BloodRequestListView.as_view(), name='request_list'),
    path('inventory/requests/<int:pk>/issue/', IssueUnitsView.as_view(), name='issue_units'),
    path('inventory/requests/<int:pk>/process-issue/', process_issuance, name='process_issuance'),
]

