# Save this file exactly as: core/urls.py
from django.urls import path
from .views import SystemLoginView,DashboardView, InstitutionListView, InstitutionCreateView, InstitutionUpdateView
from django.contrib.auth.views import LogoutView

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('accounts/login/', SystemLoginView.as_view(), name='login'),
    path('accounts/logout/', LogoutView.as_view(next_page='login'), name='logout'),
    path('institutions/', InstitutionListView.as_view(), name='institution_list'),
    path('institutions/add/', InstitutionCreateView.as_view(), name='institution_add'),
    path('institutions/<int:pk>/edit/', InstitutionUpdateView.as_view(), name='institution_edit'),

]