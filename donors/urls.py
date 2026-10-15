# Save this file exactly as: donors/urls.py
from django.urls import path
from .views import DonorListView, DonorDetailView, DonationCreateView,CampaignListView,DonationListView

urlpatterns = [
    path('donors/', DonorListView.as_view(), name='donor_list'),
    path('donors/<int:pk>/', DonorDetailView.as_view(), name='donor_profile'),
    path('donations/add/', DonationCreateView.as_view(), name='donation_add'),
    path('campaigns/', CampaignListView.as_view(), name='campaign_list'),

    # Donation specific actions routes
    path('donations/', DonationListView.as_view(), name='donation_list'),          # <-- List Donations

]