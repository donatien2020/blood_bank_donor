from django.shortcuts import render

# Create your views here.
from django.views.generic import ListView, CreateView, UpdateView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import ListView
from donors.models import Donation, Donor, DonationCampaign,DonationOutcome
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from donors.models import BloodUnit, LabTestResult, BloodUnitStatus

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


class DonorCreateView(LoginRequiredMixin, CreateView):
    model = Donor
    fields = [
        'national_id', 'first_name', 'last_name', 'gender',
        'date_of_birth', 'blood_group', 'phone', 'email',
        'district', 'address', 'weight_kg', 'is_active'
    ]
    template_name = "adminlte/donors/donor_form.html"
    success_url = reverse_lazy('donor_list')

    def form_valid(self, form):
        messages.success(self.request, f"Donor {form.instance.first_name} registered successfully!")
        return super().form_valid(form)


class DonorUpdateView(LoginRequiredMixin, UpdateView):
    model = Donor
    fields = [
        'national_id', 'first_name', 'last_name', 'gender',
        'date_of_birth', 'blood_group', 'phone', 'email',
        'district', 'address', 'weight_kg', 'is_active'
    ]
    template_name = "adminlte/donors/donor_form.html"

    def get_success_url(self):
        # Redirect directly back to their file summary page upon updates
        return reverse_lazy('donor_profile', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        messages.success(self.request, f"Donor file for {form.instance.first_name} updated successfully.")
        return super().form_valid(form)

class DonationCreateView(LoginRequiredMixin, CreateView):
    model = Donation
    fields = ['donor', 'campaign', 'donation_date', 'outcome', 'volume_ml', 'hemoglobin_g_dl', 'systolic_bp', 'diastolic_bp', 'deferral_reason', 'notes']
    template_name = "adminlte/donors/donation_form.html"
    success_url = reverse_lazy('donor_list')

    def get_initial(self):
        initial = super().get_initial()
        donor_id = self.request.GET.get('donor')
        if donor_id:
            initial['donor'] = get_object_or_404(Donor, pk=donor_id)
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



class CampaignCreateView(LoginRequiredMixin, CreateView):
    model = DonationCampaign
    fields = [
        'institution', 'name', 'location', 'district',
        'start_date', 'end_date', 'target_units',
        'organizer', 'status', 'description'
    ]
    template_name = "adminlte/donors/campaign_form.html"
    success_url = reverse_lazy('campaign_list')

    def form_valid(self, form):
        messages.success(self.request, f"Campaign '{form.instance.name}' has been planned successfully!")
        return super().form_valid(form)


class CampaignUpdateView(LoginRequiredMixin, UpdateView):
    model = DonationCampaign
    fields = [
        'institution', 'name', 'location', 'district',
        'start_date', 'end_date', 'target_units',
        'organizer', 'status', 'description'
    ]
    template_name = "adminlte/donors/campaign_form.html"
    success_url = reverse_lazy('campaign_list')

    def form_valid(self, form):
        messages.success(self.request, f"Campaign '{form.instance.name}' configuration updated.")
        return super().form_valid(form)

@login_required
@require_POST
def clear_blood_unit(request, pk):
    # Locate the blood unit package safely
    unit = get_object_or_404(BloodUnit, pk=pk)

    if unit.test_result == LabTestResult.PENDING:
        unit.test_result = LabTestResult.CLEARED
        unit.status = BloodUnitStatus.AVAILABLE
        unit.save(update_fields=['test_result', 'status'])
        messages.success(request, f"Blood Unit {unit.unit_number} has been successfully cleared and moved to available stock inventory.")
    else:
        messages.info(request, f"Blood Unit {unit.unit_number} is already processed.")

    # Redirect cleanly straight back to the historical logs dashboard page
    return redirect('donation_list')



@login_required
@require_POST
def delete_donation_session(request, pk):
    donation = get_object_or_404(Donation, pk=pk)

    # Safety Check: Stop execution if the blood unit was already dispatched to a hospital
    if hasattr(donation, 'blood_unit') and donation.blood_unit.status == 'ISSUED':
        messages.error(request, "CRITICAL ERROR: Cannot cancel this session. The related blood unit has already been issued to a hospital request.")
        return redirect('donation_list')

    try:
        with transaction.atomic():
            # 1. Manually drop the protected blood unit record row first
            if hasattr(donation, 'blood_unit'):
                donation.blood_unit.delete()

            # 2. Safely wipe the core session logs row second
            donation.delete()

        messages.success(request, "Donation session record and its matching inventory unit were successfully deleted.")
    except Exception as e:
        messages.error(request, f"Database transaction fault: {e}")

    return redirect('donation_list')

