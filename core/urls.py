# Save this file exactly as: core/urls.py
from django.urls import path
from .views import DashboardView, SystemLoginView
from django.contrib.auth.views import LogoutView

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('accounts/login/', SystemLoginView.as_view(), name='login'),
    path('accounts/logout/', LogoutView.as_view(next_page='login'), name='logout'),
]