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
from donors.models import Donor, Donation, DonationCampaign, DonationOutcome, BloodUnit, BloodUnitStatus, LabTestResult
from inventory.models import BloodRequest, IssuedUnit, StockThreshold, BloodRequestStatus

class DashboardView(LoginRequiredMixin, TemplateView):
    """Operational dashboard plus the predictions from the trained models.

    Prediction panels are drawn in the browser from the same JSON that
    /api/v1/predictions/dashboard/ serves, and refresh every 30 seconds.
    """

    template_name = "adminlte/dashboard.html"

    def get_context_data(self, **kwargs):
        from analytics.services import dashboard_payload

        context = super().get_context_data(**kwargs)
        today = timezone.localdate()

        # Minimum-stock thresholds set by the blood bank
        available = dict(
            BloodUnit.objects.available().values_list("blood_group").annotate(n=Count("pk"))
        )
        context["low_stock_alerts"] = [
            {"blood_group": t.blood_group, "current": available.get(t.blood_group, 0), "minimum": t.minimum_units}
            for t in StockThreshold.objects.all()
            if available.get(t.blood_group, 0) < t.minimum_units
        ]
        context["payload"] = dashboard_payload()
        context["expiring_units"] = BloodUnit.objects.expiring_soon(days=7)[:10]
        context["recent_requests"] = BloodRequest.objects.select_related("requestor").order_by("-request_date")[:8]
        context["active_campaigns"] = DonationCampaign.objects.filter(
            end_date__gte=today, status__in=["PLANNED", "ONGOING"]
        ).order_by("start_date")[:5]
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
