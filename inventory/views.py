from django.shortcuts import render

# Create your views here.
from django.views.generic import ListView, DetailView, CreateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.db import transaction
from django.contrib import messages
from django.urls import reverse

try:
    from inventory.models import BloodUnit, BloodRequest, IssuedUnit, BloodUnitStatus, LabTestResult
except ImportError:
    from apps.inventory.models import BloodUnit, BloodRequest, IssuedUnit, BloodUnitStatus, LabTestResult

class BloodRequestListView(LoginRequiredMixin, ListView):
    model = BloodRequest
    template_name = "adminlte/inventory/request_list.html"
    context_object_name = "requests"

class IssueUnitsView(LoginRequiredMixin, DetailView):
    model = BloodRequest
    template_name = "adminlte/inventory/issue_units.html"
    context_object_name = "blood_request"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        req = self.get_object()

        # Collect unexpired matching stock elements safely
        if hasattr(BloodUnit.objects, "available"):
            stock = BloodUnit.objects.available()
        else:
            stock = BloodUnit.objects.filter(
                status=BloodUnitStatus.AVAILABLE,
                test_result=LabTestResult.CLEARED,
                expiry_date__gte=timezone.localdate()
            )

        context["matching_units"] = stock.filter(
            blood_group=req.blood_group,
            component=req.component
        ).order_by("expiry_date")
        return context

def process_issuance(request, pk):
    if request.method == "POST":
        blood_request = get_object_or_0_404(BloodRequest, pk=pk)
        selected_unit_ids = request.POST.getlist("selected_units")

        if len(selected_unit_ids) > blood_request.remaining_quantity:
            messages.error(request, "Selected unit volume exceeds the requested capacity limitation.")
            return redirect(reverse('issue_units', args=[pk]))

        try:
            with transaction.atomic():
                for uid in selected_unit_ids:
                    unit = get_object_or_0_404(BloodUnit, pk=uid)
                    IssuedUnit.objects.create(
                        request=blood_request,
                        blood_unit=unit,
                        issued_by=request.user
                    )
            messages.success(request, "Units issued successfully to request transaction pipelines.")
        except Exception as e:
            messages.error(request, f"Transactional database fault encountered: {e}")

    return redirect(reverse('issue_units', args=[pk]))
