from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Institution
from donors.models import BloodUnitStatus, LabTestResult
from donors.tests import make_donation, make_donor
from inventory.models import BloodRequest, BloodRequestStatus


def make_institution(name, category="HOSPITAL", **overrides):
    values = dict(name=name, institution_type="DISTRICT" if category == "HOSPITAL" else "BLOOD_BANK",
                  institution_category=category, district="Gasabo", contact_person="Lab",
                  phone="0788000000")
    values.update(overrides)
    return Institution.objects.create(**values)


class IssuingTests(TestCase):
    def setUp(self):
        self.hospital = make_institution("Test Hospital")
        self.bank = make_institution("Test Blood Bank", category="BLOOD_BANK")
        self.user = get_user_model().objects.create_user("staff", password="pass-1234-x")
        donation = make_donation(make_donor(), days_ago=3)
        self.unit = donation.blood_unit
        self.unit.test_result = LabTestResult.CLEARED
        self.unit.save()
        self.request = BloodRequest.objects.create(
            requestor=self.hospital, receiver=self.bank, requested_by="Dr. Test", patient_reference="PT-1",
            blood_group="O+", component="WB", quantity_units=1, required_by=timezone.localdate(),
        )

    def test_issuing_requires_login(self):
        url = reverse("process_issuance", args=[self.request.pk])
        response = self.client.post(url, {"selected_units": [self.unit.pk]})
        self.assertEqual(response.status_code, 302)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, BloodUnitStatus.AVAILABLE)

    def test_issue_and_remove_unit(self):
        self.client.force_login(self.user)
        self.client.post(reverse("process_issuance", args=[self.request.pk]), {"selected_units": [self.unit.pk]})
        self.unit.refresh_from_db()
        self.request.refresh_from_db()
        self.assertEqual(self.unit.status, BloodUnitStatus.ISSUED)
        self.assertEqual(self.request.status, BloodRequestStatus.FULFILLED)

        issued = self.request.issued_units.get()
        self.assertEqual(self.client.get(reverse("remove_issued_unit", args=[issued.pk])).status_code, 405)
        self.client.post(reverse("remove_issued_unit", args=[issued.pk]))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, BloodUnitStatus.AVAILABLE)

    def test_inactive_requestor_shows_form_error_not_server_error(self):
        self.client.force_login(self.user)
        inactive = make_institution("Closed Clinic", is_active=False)
        response = self.client.post(reverse("request_add"), {
            "requestor": inactive.pk, "receiver": self.bank.pk, "requested_by": "Dr. X",
            "patient_reference": "PT-2", "blood_group": "O+", "component": "WB", "quantity_units": 1,
            "urgency": "ROUTINE", "required_by": (timezone.localdate() + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(response.status_code, 200)  # used to be a 500
        self.assertIn("requestor", response.context["form"].errors)
