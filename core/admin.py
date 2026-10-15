from django.contrib import admin

# Register your models here.
# Save inside: core/admin.py
from django.contrib import admin
from .models import Institution, InstitutionMembership

@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
    list_display = ('name', 'institution_type', 'institution_category', 'district', 'is_active')
    list_filter = ('institution_type', 'institution_category', 'is_active', 'district')
    search_fields = ('name', 'contact_person', 'phone', 'email')
    ordering = ('name',)

@admin.register(InstitutionMembership)
class InstitutionMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'institution')
    list_filter = ('institution',)
    search_fields = ('user__username', 'institution__name')
