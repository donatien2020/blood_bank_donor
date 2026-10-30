"""Turn database rows into plain JSON-friendly dicts.

The API returns these dicts, and the prediction code builds features from
the same dicts. Training (which reads the API) and live prediction (which
reads the database) therefore always see the data in the same shape.
"""

from django.utils import timezone


def _iso(value):
    return value.isoformat() if value is not None else None


def donor_record(donor):
    return {
        "id": donor.pk,
        "national_id": donor.national_id,
        "first_name": donor.first_name,
        "last_name": donor.last_name,
        "gender": donor.gender,
        "date_of_birth": _iso(donor.date_of_birth),
        "blood_group": donor.blood_group,
        "phone": donor.phone,
        "district": donor.district,
        "weight_kg": float(donor.weight_kg),
        "is_active": donor.is_active,
        "registered_at": _iso(donor.registered_at),
    }


def donation_record(donation):
    return {
        "id": donation.pk,
        "donor_id": donation.donor_id,
        "campaign_id": donation.campaign_id,
        "donation_date": _iso(donation.donation_date),
        "outcome": donation.outcome,
        "volume_ml": donation.volume_ml,
        "hemoglobin_g_dl": float(donation.hemoglobin_g_dl),
        "systolic_bp": donation.systolic_bp,
        "diastolic_bp": donation.diastolic_bp,
        "deferral_reason": donation.deferral_reason,
    }


def blood_request_record(blood_request):
    local_dt = timezone.localtime(blood_request.request_date)
    return {
        "id": blood_request.pk,
        "request_number": blood_request.request_number,
        "requestor_id": blood_request.requestor_id,
        "receiver_id": blood_request.receiver_id,
        "blood_group": blood_request.blood_group,
        "component": blood_request.component,
        "quantity_units": blood_request.quantity_units,
        "urgency": blood_request.urgency,
        "status": blood_request.status,
        "request_date": _iso(local_dt),
        "request_day": _iso(local_dt.date()),
        "required_by": _iso(blood_request.required_by),
    }


def blood_unit_record(unit):
    return {
        "id": unit.pk,
        "unit_number": unit.unit_number,
        "donation_id": unit.donation_id,
        "blood_group": unit.blood_group,
        "component": unit.component,
        "collection_date": _iso(unit.collection_date),
        "expiry_date": _iso(unit.expiry_date),
        "status": unit.status,
        "test_result": unit.test_result,
    }
