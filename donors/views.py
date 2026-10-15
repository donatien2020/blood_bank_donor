from django.shortcuts import render

# Create your views here.
from django.views.generic import ListView, CreateView, UpdateView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import ListView


try:
    from donors.models import Donation, Donor, DonationCampaign
except ImportError:
    from apps.donors.models import Donation, Donor, DonationCampaign

try:
    from donors.models import Donor, Donation, DonationCampaign, DonationOutcome
except ImportError:
    from apps.donors.models import Donor, Donation, DonationCampaign, DonationOutcome

class DonorListView(LoginRequiredMixin, ListView):
    model = Donor
    template_name = "adminlte/donors/donor_list.html"
    context_object_name = "donors"
    paginate_by = 15

    def get_queryset(self):
        queryset = super().get_queryset()
        search_query = self.request.GET.get("search", "").strip()
        blood_group = self.request.GET.get("blood_group", "").strip()
        district = self.request.GET.get("district", "").strip()

        if search_query:
            queryset = queryset.filter(
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query) |
                Q(national_id__icontains=search_query)
            )
        if blood_group:
            queryset = queryset.filter(blood_group=blood_group)
        if district:
            queryset = queryset.filter(district__icontains=district)
        return queryset

class DonorDetailView(LoginRequiredMixin, DetailView):
    model = Donor
    template_name = "adminlte/donors/donor_profile.html"
    context_object_name = "donor"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        donor = self.get_object()
        context["history"] = donor.donations.all().order_by("-donation_date")
        context["is_eligible"] = donor.is_eligible_on(timezone.localdate())
        return context

class DonationCreateView(LoginRequiredMixin, CreateView):
    model = Donation
    fields = ['donor', 'campaign', 'donation_date', 'outcome', 'volume_ml', 'hemoglobin_g_dl', 'systolic_bp', 'diastolic_bp', 'deferral_reason', 'notes']
    template_name = "adminlte/donors/donation_form.html"
    success_url = reverse_lazy('donor_list')

    def get_initial(self):
        initial = super().get_initial()
        donor_id = self.request.GET.get('donor')
        if donor_id:
            initial['donor'] = get_object_or_0_404(Donor, pk=donor_id)
        return initial

    def form_valid(self, form):
        form.instance.recorded_by = self.request.user
        try:
            return super().form_valid(form)
        except Exception as e:
            form.add_error(None, f"Business Validation Failed: {e}")
            return self.form_invalid(form)

class CampaignListView(LoginRequiredMixin, ListView):
    model = DonationCampaign
    template_name = "adminlte/donors/campaign_list.html"
    context_object_name = "campaigns"
    ordering = ['-start_date']




# 1. View to List All Donation Records
class DonationListView(LoginRequiredMixin, ListView):
    model = Donation
    template_name = "adminlte/donors/donation_list.html"
    context_object_name = "donations"
    paginate_by = 15
    ordering = ["-donation_date", "-id"]


