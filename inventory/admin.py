from django.contrib import admin

# Register your models here.
# Save inside: inventory/admin.py
from django.contrib import admin
from .models import BloodUnit, BloodRequest, IssuedUnit, StockThreshold

# This allows you to add/see issued units directly inside the Blood Request panel!
class IssuedUnitInline(admin.TabularInline):
    model = IssuedUnit
    extra = 0
    raw_id_fields = ('blood_unit',)

@admin.register(BloodUnit)
class BloodUnitAdmin(admin.ModelAdmin):
    list_display = ('unit_number', 'blood_group', 'component', 'collection_date', 'expiry_date', 'status', 'test_result')
    list_filter = ('blood_group', 'component', 'status', 'test_result', 'expiry_date')
    search_fields = ('unit_number', 'storage_location')
    readonly_fields = ('unit_number', 'blood_group', 'collection_date')
    ordering = ('expiry_date', 'unit_number')

@admin.register(BloodRequest)
class BloodRequestAdmin(admin.ModelAdmin):
    list_display = ('request_number', 'get_requestor_name', 'blood_group', 'component', 'quantity_units', 'urgency', 'status', 'required_by')
    list_filter = ('status', 'urgency', 'blood_group', 'component', 'required_by')
    search_fields = ('request_number', 'requested_by', 'patient_reference')
    ordering = ('-request_date',)
    inlines = [IssuedUnitInline]

    # Dynamic fallback helper method to avoid model attribute errors between requestor vs institution fields
    def get_requestor_name(self, obj):
        if hasattr(obj, 'requestor') and obj.requestor:
            return obj.requestor.name
        elif hasattr(obj, 'institution') and obj.institution:
            return obj.institution.name
        return "Unknown"
    get_requestor_name.short_description = 'Requestor Institution'

@admin.register(IssuedUnit)
class IssuedUnitAdmin(admin.ModelAdmin):
    list_display = ('blood_unit', 'request', 'issued_date', 'issued_by')
    list_filter = ('issued_date',)
    search_fields = ('blood_unit__unit_number', 'request__request_number')

@admin.register(StockThreshold)
class StockThresholdAdmin(admin.ModelAdmin):
    list_display = ('blood_group', 'minimum_units', 'current_stock', 'is_low_stock')
    ordering = ('blood_group',)
