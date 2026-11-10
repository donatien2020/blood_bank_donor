from django.urls import path

from . import api, views

urlpatterns = [
    # Web pages
    path("screening/", views.ScreeningView.as_view(), name="screening"),
    path("analytics/forecasts/", views.DemandForecastView.as_view(), name="analytics_forecasts"),
    path("analytics/donor-predictions/", views.DonorPredictionListView.as_view(), name="analytics_donor_predictions"),
    path("analytics/screenings/", views.ScreeningCheckListView.as_view(), name="analytics_screenings"),
    path("analytics/models/", views.TrainedModelListView.as_view(), name="analytics_models"),
    path("analytics/models/<int:pk>/", views.TrainedModelDetailView.as_view(), name="analytics_model_detail"),
    path("analytics/models/<int:pk>/activate/", views.activate_model, name="analytics_model_activate"),
    path("analytics/models/retrain/", views.retrain_models, name="analytics_retrain"),
    path("analytics/refresh/", views.refresh_predictions, name="analytics_refresh"),

    # JSON API - read
    path("api/v1/donors/", api.donors_list, name="api_donors"),
    path("api/v1/donations/", api.donations_list, name="api_donations"),
    path("api/v1/blood-requests/", api.blood_requests_list, name="api_blood_requests"),
    path("api/v1/blood-units/", api.blood_units_list, name="api_blood_units"),

    # JSON API - new incoming data
    path("api/v1/ingest/donors/", api.ingest_donor, name="api_ingest_donor"),
    path("api/v1/ingest/donations/", api.ingest_donation, name="api_ingest_donation"),
    path("api/v1/ingest/blood-requests/", api.ingest_blood_request, name="api_ingest_blood_request"),
    path("api/v1/ingest/screenings/", api.ingest_screening, name="api_ingest_screening"),

    # JSON API - predictions
    path("api/v1/predictions/dashboard/", api.dashboard_predictions, name="api_dashboard"),
    path("api/v1/predictions/donors/<int:pk>/", api.donor_prediction, name="api_donor_prediction"),
]
