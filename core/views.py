from django.shortcuts import render

# Create your views here.
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from django.utils import timezone
from django.db.models import Count, Sum, Q
from datetime import timedelta

from django.views.generic import ListView, CreateView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.contrib import messages
from core.models import Institution
try:
    from donors.models import Donor, Donation, DonationCampaign, DonationOutcome
    from inventory.models import BloodUnit, BloodRequest, IssuedUnit, StockThreshold, BloodRequestStatus, BloodUnitStatus, LabTestResult
except ImportError:
    from apps.donors.models import Donor, Donation, DonationCampaign, DonationOutcome
    from apps.inventory.models import BloodUnit, BloodRequest, IssuedUnit, StockThreshold, BloodRequestStatus, BloodUnitStatus, LabTestResult

class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "adminlte/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        first_of_month = today.replace(day=1)

        # 1. Summary Cards Data
        context["total_donors"] = Donor.objects.filter(is_active=True).count()
        context["donations_this_month"] = Donation.objects.filter(
            donation_date__gte=first_of_month,
            outcome=DonationOutcome.COLLECTED
        ).count()

        # Safe execution of custom manager methods
        if hasattr(BloodUnit.objects, "available"):
            context["available_units"] = BloodUnit.objects.available().count()
        else:
            context["available_units"] = BloodUnit.objects.filter(
                status=BloodUnitStatus.AVAILABLE,
                test_result=LabTestResult.CLEARED,
                expiry_date__gte=today
            ).count()

        context["pending_requests"] = BloodRequest.objects.filter(
            status__in=[BloodRequestStatus.PENDING, BloodRequestStatus.PARTIAL]
        ).count()

        if hasattr(BloodUnit.objects, "expiring_soon"):
            context["expiring_soon"] = BloodUnit.objects.expiring_soon(days=7).count()
        else:
            context["expiring_soon"] = BloodUnit.objects.filter(
                status=BloodUnitStatus.AVAILABLE,
                test_result=LabTestResult.CLEARED,
                expiry_date__gte=today,
                expiry_date__lte=today + timedelta(days=7)
            ).count()

        # 2. Low-Stock Alerts Threshold Verification
        thresholds = StockThreshold.objects.all()
        low_stock_alerts = []
        for t in thresholds:
            if hasattr(BloodUnit.objects, "available"):
                current = BloodUnit.objects.available().filter(blood_group=t.blood_group).count()
            else:
                current = BloodUnit.objects.filter(
                    status=BloodUnitStatus.AVAILABLE,
                    test_result=LabTestResult.CLEARED,
                    expiry_date__gte=today,
                    blood_group=t.blood_group
                ).count()
            if current < t.minimum_units:
                low_stock_alerts.append({
                    "blood_group": t.blood_group,
                    "current": current,
                    "minimum": t.minimum_units
                })
        context["low_stock_alerts"] = low_stock_alerts

        # 3. Dynamic Charts Aggregation Data
        # Available stock per group
        if hasattr(BloodUnit.objects, "stock_by_group"):
            stock_query = list(BloodUnit.objects.stock_by_group())
        else:
            stock_query = list(BloodUnit.objects.filter(
                status=BloodUnitStatus.AVAILABLE,
                test_result=LabTestResult.CLEARED,
                expiry_date__gte=today
            ).values("blood_group").annotate(units=Count("id")))

        context["stock_labels"] = [item["blood_group"] for item in stock_query]
        context["stock_data"] = [item.get("units", item.get("id__count", 0)) for item in stock_query]

        # Recent Records Tables
        context["recent_donations"] = Donation.objects.all().select_related("donor")[:10]
        context["recent_requests"] = BloodRequest.objects.all().order_by("-request_date")[:10]

        return context

class SystemLoginView(LoginView):
    template_name = "adminlte/login.html"
    redirect_authenticated_user = True



class InstitutionListView(LoginRequiredMixin, ListView):
    model = Institution
    template_name = "adminlte/core/institution_list.html"
    context_object_name = "institutions"
    paginate_by = 10
    ordering = ["name"]

class InstitutionCreateView(LoginRequiredMixin, CreateView):
    model = Institution
    fields = [
        'name', 'institution_type', 'institution_category',
        'district', 'address', 'contact_person', 'phone',
        'email', 'is_active'
    ]
    template_name = "adminlte/core/institution_form.html"
    success_url = reverse_lazy('institution_list')

    def form_valid(self, form):
        messages.success(self.request, f"Institution '{form.instance.name}' registered successfully!")
        return super().form_valid(form)

class InstitutionUpdateView(LoginRequiredMixin, UpdateView):
    model = Institution
    fields = [
        'name', 'institution_type', 'institution_category',
        'district', 'address', 'contact_person', 'phone',
        'email', 'is_active'
    ]
    template_name = "adminlte/core/institution_form.html"
    success_url = reverse_lazy('institution_list')

    def form_valid(self, form):
        messages.success(self.request, f"Institution parameters updated for '{form.instance.name}'.")
        return super().form_valid(form)













































#ORIGINAL VIEWS


@login_required(login_url="/admin/login/")
def dashboard(request):
    context = {
        "total_donors": 10,
        "total_donations": 20,
        "total_requests": 12,
        "available_units": 18,
    }

    return render(request, "adminlte/index.html", context)

@login_required(login_url="/admin/login/")
def dashboardV1(request):
    context = {
        "total_donors": 10,
        "total_donations": 20,
        "total_requests": 12,
        "available_units": 18,
    }

    return render(request, "adminlte/index.html", context)
@login_required(login_url="/admin/login/")
def dashboardV2(request):
    context = {
        "total_donors": 10,
        "total_donations": 20,
        "total_requests": 12,
        "available_units": 18,
    }

    return render(request, "adminlte/index2.html", context)

@login_required(login_url="/admin/login/")
def dashboardV3(request):
    context = {
        "total_donors": 10,
        "total_donations": 20,
        "total_requests": 12,
        "available_units": 18,
    }

    return render(request, "adminlte/index3.html", context)

