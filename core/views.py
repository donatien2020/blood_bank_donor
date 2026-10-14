from django.shortcuts import render

# Create your views here.
from django.shortcuts import render
from django.contrib.auth.decorators import login_required


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

