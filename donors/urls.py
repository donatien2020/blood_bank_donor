# Save this file exactly as: donors/urls.py
from django.urls import path
from . import views
from .views import DonorListView, DonorDetailView,DonorCreateView,DonorUpdateView, DonationCreateView,CampaignListView,DonationListView,CampaignCreateView,CampaignUpdateView,delete_donation_session
urlpatterns = [



    path('donors/', DonorListView.as_view(), name='donor_list'),
    path('donors/<int:pk>/', DonorDetailView.as_view(), name='donor_profile'),
    path('donors/add/', DonorCreateView.as_view(), name='donor_add'),
    path('donors/<int:pk>/edit/', DonorUpdateView.as_view(), name='donor_edit'),
    path('campaigns/', CampaignListView.as_view(), name='campaign_list'),
    path('campaigns/add/', CampaignCreateView.as_view(), name='campaign_add'),
    path('campaigns/<int:pk>/edit/', CampaignUpdateView.as_view(), name='campaign_edit'),
    path('donations/', DonationListView.as_view(), name='donation_list'),          # <-- List Donations
    path('donations/add/', DonationCreateView.as_view(), name='donation_add'),
    path('donations/<int:pk>/delete/', delete_donation_session, name='delete_donation'),

]
