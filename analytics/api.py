"""JSON API for the blood bank (plain Django, no extra packages).

Read endpoints (GET) - used by the training pipeline and external systems:
    /api/v1/donors/              /api/v1/donations/
    /api/v1/blood-requests/      /api/v1/blood-units/

Ingest endpoints (POST) - new incoming data; each returns fresh predictions:
    /api/v1/ingest/donors/       /api/v1/ingest/donations/
    /api/v1/ingest/blood-requests/
    /api/v1/ingest/screenings/   (pre-donation check -> deferral risk)

Prediction endpoints (GET):
    /api/v1/predictions/dashboard/   everything the dashboard shows
    /api/v1/predictions/donors/<id>/

Authentication: a logged-in session, or the header
"X-API-Key: <settings.ANALYTICS_API_KEY>". POST requests always need the key.
"""

import hmac
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.models import Institution
from donors.models import BloodUnit, Donation, Donor
from inventory.models import BloodRequest

from . import serializers, services

DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 2000


# ---------------------------------------------------------------- helpers ---

def _has_valid_key(request):
    supplied = request.headers.get("X-API-Key", "")
    expected = settings.ANALYTICS_API_KEY
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def api_read_access(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated) or _has_valid_key(request):
            return view(request, *args, **kwargs)
        return JsonResponse({"error": "Authentication required (login or X-API-Key header)."}, status=401)
    return wrapper


def api_write_access(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not _has_valid_key(request):
            return JsonResponse({"error": "A valid X-API-Key header is required."}, status=401)
        return view(request, *args, **kwargs)
    return csrf_exempt(wrapper)


def _json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValidationError("Request body must be valid JSON.")


def _validation_error_response(error):
    if hasattr(error, "message_dict"):
        return JsonResponse({"errors": error.message_dict}, status=400)
    return JsonResponse({"errors": {"__all__": error.messages}}, status=400)


def _paginate(request, queryset, serializer):
    try:
        page = max(int(request.GET.get("page", 1)), 1)
        size = min(max(int(request.GET.get("page_size", DEFAULT_PAGE_SIZE)), 1), MAX_PAGE_SIZE)
    except ValueError:
        return JsonResponse({"error": "page and page_size must be integers."}, status=400)
    total = queryset.count()
    offset = (page - 1) * size
    rows = [serializer(obj) for obj in queryset[offset : offset + size]]
    next_url = None
    if offset + size < total:
        query = request.GET.copy()
        query["page"] = page + 1
        query["page_size"] = size
        next_url = f"{request.path}?{query.urlencode()}"
    return JsonResponse({"count": total, "page": page, "next": next_url, "results": rows})


def _date_filters(request, queryset, field):
    for param, lookup in (("since", "gte"), ("until", "lte")):
        value = request.GET.get(param)
        if value:
            queryset = queryset.filter(**{f"{field}__{lookup}": date.fromisoformat(value)})
    return queryset


def _decimal(value):
    """JSON numbers arrive as floats (13.3 -> 13.2999...); go through str."""
    if value in (None, ""):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"'{value}' is not a valid number.")


def _find_donor(data):
    if data.get("donor_id"):
        return Donor.objects.filter(pk=data["donor_id"]).first()
    if data.get("national_id"):
        return Donor.objects.filter(national_id=str(data["national_id"])).first()
    return None


# ------------------------------------------------------------ read: GET ---

@require_GET
@api_read_access
def donors_list(request):
    return _paginate(request, Donor.objects.order_by("pk"), serializers.donor_record)


@require_GET
@api_read_access
def donations_list(request):
    try:
        qs = _date_filters(request, Donation.objects.order_by("pk"), "donation_date")
    except ValueError:
        return JsonResponse({"error": "Dates must be YYYY-MM-DD."}, status=400)
    return _paginate(request, qs, serializers.donation_record)


@require_GET
@api_read_access
def blood_requests_list(request):
    try:
        qs = _date_filters(request, BloodRequest.objects.order_by("pk"), "request_date__date")
    except ValueError:
        return JsonResponse({"error": "Dates must be YYYY-MM-DD."}, status=400)
    return _paginate(request, qs, serializers.blood_request_record)


@require_GET
@api_read_access
def blood_units_list(request):
    qs = BloodUnit.objects.order_by("pk")
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET["status"])
    return _paginate(request, qs, serializers.blood_unit_record)


# ---------------------------------------------------------- ingest: POST ---

@require_POST
@api_write_access
def ingest_donor(request):
    try:
        data = _json_body(request)
        fields = ["national_id", "first_name", "last_name", "gender", "date_of_birth",
                  "blood_group", "phone", "email", "district", "address", "weight_kg"]
        donor = Donor(**{f: data[f] for f in fields if f in data})
        donor.weight_kg = _decimal(donor.weight_kg)
        if isinstance(donor.date_of_birth, str):
            donor.date_of_birth = date.fromisoformat(donor.date_of_birth)
        donor.save()  # Donor.save() runs full validation
    except ValidationError as error:
        return _validation_error_response(error)
    except (TypeError, ValueError) as error:
        return JsonResponse({"errors": {"__all__": [str(error)]}}, status=400)
    return JsonResponse({"donor": serializers.donor_record(donor)}, status=201)


@require_POST
@api_write_access
def ingest_donation(request):
    try:
        data = _json_body(request)
        donor = _find_donor(data)
        if donor is None:
            return JsonResponse({"errors": {"donor": ["Donor not found (send donor_id or national_id)."]}}, status=400)
        donation_date = date.fromisoformat(data["donation_date"]) if data.get("donation_date") else timezone.localdate()
        donation = Donation(
            donor=donor,
            campaign_id=data.get("campaign_id"),
            donation_date=donation_date,
            outcome=data.get("outcome", "COLLECTED"),
            volume_ml=data.get("volume_ml", 450),
            hemoglobin_g_dl=_decimal(data.get("hemoglobin_g_dl")),
            systolic_bp=data.get("systolic_bp"),
            diastolic_bp=data.get("diastolic_bp"),
            deferral_reason=data.get("deferral_reason", ""),
            notes=data.get("notes", ""),
        )
        with transaction.atomic():
            donation.save()  # validates eligibility and creates the blood unit
    except ValidationError as error:
        return _validation_error_response(error)
    except (TypeError, ValueError, KeyError) as error:
        return JsonResponse({"errors": {"__all__": [str(error)]}}, status=400)

    # Predictions refresh right away so the response can include them.
    prediction = services.refresh_donor_prediction(donor)
    unit = BloodUnit.objects.filter(donation=donation).first()
    return JsonResponse({
        "donation": serializers.donation_record(donation),
        "blood_unit": serializers.blood_unit_record(unit) if unit else None,
        "predictions": {
            "donor_return_probability": prediction.return_probability if prediction else None,
            "next_eligible_date": donor.next_eligible_date.isoformat() if donor.next_eligible_date else None,
        },
    }, status=201)


@require_POST
@api_write_access
def ingest_blood_request(request):
    try:
        data = _json_body(request)
        blood_request = BloodRequest(
            requestor=get_object_or_404(Institution, pk=data.get("requestor_id")),
            receiver=get_object_or_404(Institution, pk=data.get("receiver_id")),
            requested_by=data.get("requested_by", "API"),
            patient_reference=data.get("patient_reference", ""),
            blood_group=data.get("blood_group"),
            component=data.get("component", "WB"),
            quantity_units=data.get("quantity_units"),
            urgency=data.get("urgency", "ROUTINE"),
            required_by=date.fromisoformat(data["required_by"]) if data.get("required_by") else timezone.localdate(),
            remarks=data.get("remarks", ""),
        )
        blood_request.save()
    except ValidationError as error:
        return _validation_error_response(error)
    except (TypeError, ValueError) as error:
        return JsonResponse({"errors": {"__all__": [str(error)]}}, status=400)

    forecasts = services.refresh_demand_forecasts()
    forecast = forecasts.get(blood_request.blood_group)
    return JsonResponse({
        "blood_request": serializers.blood_request_record(blood_request),
        "predictions": {"demand_forecast": services.forecast_dict(forecast) if forecast else None},
    }, status=201)


@require_POST
@api_write_access
def ingest_screening(request):
    """Score a donor *before* collection: how likely is a deferral?"""
    try:
        data = _json_body(request)
        donor = _find_donor(data)
        if donor is None:
            return JsonResponse({"errors": {"donor": ["Donor not found (send donor_id or national_id)."]}}, status=400)
        if data.get("systolic_bp") is None or data.get("diastolic_bp") is None:
            return JsonResponse({"errors": {"blood_pressure": ["systolic_bp and diastolic_bp are required."]}}, status=400)
        screening = services.screen_donor(
            donor,
            screening_date=date.fromisoformat(data["screening_date"]) if data.get("screening_date") else None,
            systolic_bp=int(data["systolic_bp"]),
            diastolic_bp=int(data["diastolic_bp"]),
            weight_kg=data.get("weight_kg"),
            source=str(data.get("source", "api"))[:60],
        )
    except services.ModelNotTrained as error:
        return JsonResponse({"error": str(error)}, status=503)
    except (TypeError, ValueError) as error:
        return JsonResponse({"errors": {"__all__": [str(error)]}}, status=400)
    return JsonResponse({"screening": services.screening_dict(screening)}, status=201)


# ------------------------------------------------------ predictions: GET ---

@require_GET
@api_read_access
def dashboard_predictions(request):
    return JsonResponse(services.dashboard_payload())


@require_GET
@api_read_access
def donor_prediction(request, pk):
    donor = get_object_or_404(Donor, pk=pk)
    prediction = services.refresh_donor_prediction(donor)
    return JsonResponse({
        "donor": serializers.donor_record(donor),
        "return_probability": prediction.return_probability if prediction else None,
        "eligible_today": donor.is_eligible_on(),
        "next_eligible_date": donor.next_eligible_date.isoformat() if donor.next_eligible_date else None,
    })
