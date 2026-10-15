from django.contrib import admin

# Register your models here.
# Save inside: donors/admin.py
from django.contrib import admin
from .models import Donor, DonationCampaign, Donation

@admin.register(Donor)
class DonorAdmin(admin.ModelAdmin):
    list_display = ('national_id', 'first_name', 'last_name', 'blood_group', 'gender', 'district', 'is_active')
    list_filter = ('blood_group', 'gender', 'is_active', 'district')
    search_fields = ('national_id', 'first_name', 'last_name', 'phone')
    ordering = ('last_name', 'first_name')

@admin.register(DonationCampaign)
class DonationCampaignAdmin(admin.ModelAdmin):
    list_display = ('name', 'institution', 'district', 'start_date', 'end_date', 'status', 'target_units')
    list_filter = ('status', 'district', 'start_date')
    search_fields = ('name', 'location', 'organizer')
    ordering = ('-start_date',)

@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ('id', 'donor', 'donation_date', 'outcome', 'hemoglobin_g_dl', 'volume_ml', 'recorded_by')
    list_filter = ('outcome', 'donation_date')
    search_fields = ('donor__first_name', 'donor__last_name', 'donor__national_id', 'deferral_reason')
    ordering = ('-donation_date', '-id')
