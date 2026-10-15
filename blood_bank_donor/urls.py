"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include

import donors
from core.views import dashboard,dashboardV1,dashboardV2,dashboardV3
urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include('core.urls')),
    path("donors/", include('donors.urls')),
    path("inventory/", include('inventory.urls')),
    path("dashboard/", dashboard, name="dashboard"),
    path(
        'dashboard/index.html',
        dashboardV1,
        name='adminLteDashboard V1'
    ),
    path(
        'dashboard/index2.html',
        dashboardV2,
        name='adminLteDashboardV2'
    ),path(
        'dashboard/index3.html',
        dashboardV3,
        name='adminLteDashboardV3'
    ),
]