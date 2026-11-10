# Save this file exactly as: inventory/urls.py
from django.urls import path
from .views import (
    BloodRequestListView,
    BloodRequestCreateView,
    BloodRequestUpdateView,
    IssueUnitsView,
    process_issuance,remove_issued_unit
)

urlpatterns = [
    path('inventory/requests/', BloodRequestListView.as_view(), name='request_list'),
    path('inventory/requests/add/', BloodRequestCreateView.as_view(), name='request_add'),
    path('inventory/requests/<int:pk>/edit/', BloodRequestUpdateView.as_view(), name='request_edit'),
    path('inventory/requests/<int:pk>/issue/', IssueUnitsView.as_view(), name='issue_units'),
    path('inventory/requests/<int:pk>/process/', process_issuance, name='process_issuance'),
    path('inventory/issued/<int:pk>/remove/', remove_issued_unit, name='remove_issued_unit'),

]


