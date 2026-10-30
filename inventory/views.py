from django.shortcuts import render

# Create your views here.
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.db import transaction
from django.contrib import messages
from django.urls import reverse
from django.urls import reverse_lazy, reverse
from inventory.models import BloodRequest, IssuedUnit
from donors.models import BloodUnit
from django.contrib.auth.decorators import login_required


class BloodRequestListView(LoginRequiredMixin, ListView):
    model = BloodRequest
    template_name = "adminlte/inventory/request_list.html"
    context_object_name = "requests"
    paginate_by = 15
    ordering = ["-request_date"]

class IssueUnitsView(LoginRequiredMixin, DetailView):
    model = BloodRequest
    template_name = "adminlte/inventory/issue_units.html"
    context_object_name = "blood_request"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        req = self.get_object()

        # Collect unexpired matching stock elements safely using custom manager
        context["matching_units"] = BloodUnit.objects.available().filter(
            blood_group=req.blood_group,
            component=req.component
        ).order_by("expiry_date")

        return context

def process_issuance(request, pk):
    if request.method == "POST":
        blood_request = get_object_or_404(BloodRequest, pk=pk)
        selected_unit_ids = request.POST.getlist("selected_units")

        if len(selected_unit_ids) > blood_request.remaining_quantity:
            messages.error(request, "Selected unit volume exceeds the requested capacity limitation.")
            return redirect(reverse('issue_units', args=[pk]))

        try:
            with transaction.atomic():
                for uid in selected_unit_ids:
                    unit = get_object_or_404(BloodUnit, pk=uid)
                    IssuedUnit.objects.create(
                        request=blood_request,
                        blood_unit=unit,
                        issued_by=request.user
                    )
            messages.success(request, "Units issued successfully to request transaction pipelines.")
        except Exception as e:
            messages.error(request, f"Transactional database fault encountered: {e}")

    return redirect(reverse('request_list'))


class BloodRequestCreateView(LoginRequiredMixin, CreateView):
    model = BloodRequest
    fields = [
        'requestor', 'receiver', 'requested_by', 'patient_reference',
        'blood_group', 'component', 'quantity_units', 'urgency',
        'required_by', 'remarks'
    ]
    template_name = "adminlte/inventory/request_form.html"
    success_url = reverse_lazy('request_list')

    def form_valid(self, form):
        messages.success(self.request, f"Blood Request {form.instance.request_number} logged successfully!")
        return super().form_valid(form)


class BloodRequestUpdateView(LoginRequiredMixin, UpdateView):
    model = BloodRequest
    fields = [
        'requestor', 'receiver', 'requested_by', 'patient_reference',
        'blood_group', 'component', 'quantity_units', 'urgency',
        'required_by', 'status', 'remarks'
    ]
    template_name = "adminlte/inventory/request_form.html"
    success_url = reverse_lazy('request_list')

    def form_valid(self, form):
        messages.success(self.request, f"Blood Request {form.instance.request_number} updated.")
        return super().form_valid(form)

@login_required
def remove_issued_unit(request, pk):
    # Locate the active issue linkage record
    issued_item = get_object_or_404(IssuedUnit, pk=pk)
    request_id = issued_item.request.pk
    unit_number = issued_item.blood_unit.unit_number

    # Executing .delete() automatically updates the unit status back to AVAILABLE
    issued_item.delete()

    messages.success(request, f"Unit {unit_number} successfully removed from the request allocation panel.")
    return redirect('issue_units', pk=request_id)
