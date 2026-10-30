from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from donors.models import BloodUnit, Donation, DonationOutcome, Donor, LabTestResult


def make_donor(**overrides):
    today = timezone.localdate()
    values = dict(
        national_id="1199080000000001", first_name="Alice", last_name="Uwase", gender="F",
        date_of_birth=today.replace(year=today.year - 30), blood_group="O+", phone="0781234567",
        district="Gasabo", weight_kg=Decimal("60.0"),
    )
    values.update(overrides)
    return Donor.objects.create(**values)


def make_donation(donor, days_ago=0, **overrides):
    values = dict(
        donor=donor, donation_date=timezone.localdate() - timedelta(days=days_ago),
        outcome=DonationOutcome.COLLECTED, volume_ml=450, hemoglobin_g_dl=Decimal("13.5"),
    )
    values.update(overrides)
    return Donation.objects.create(**values)


class DonorRulesTests(TestCase):
    def test_new_donor_must_be_adult(self):
        today = timezone.localdate()
        with self.assertRaises(ValidationError):
            make_donor(date_of_birth=today.replace(year=today.year - 16))

    def test_existing_donor_over_65_can_still_be_edited_and_deactivated(self):
        donor = make_donor()
        # Simulate time passing: the donor is now 66.
        Donor.objects.filter(pk=donor.pk).update(date_of_birth=date(1950, 1, 1))
        donor.refresh_from_db()
        donor.is_active = False
        donor.save()  # used to raise "Donor must be between 18 and 65"
        self.assertFalse(donor.is_eligible_on())

    def test_56_day_interval(self):
        donor = make_donor()
        make_donation(donor, days_ago=30)
        self.assertFalse(donor.is_eligible_on())
        self.assertTrue(donor.is_eligible_on(timezone.localdate() + timedelta(days=26)))


class DonationRulesTests(TestCase):
    def test_collected_donation_creates_pending_unit(self):
        donation = make_donation(make_donor())
        unit = BloodUnit.objects.get(donation=donation)
        self.assertEqual(unit.test_result, LabTestResult.PENDING)
        self.assertEqual(unit.expiry_date, donation.donation_date + timedelta(days=35))

    def test_saved_collected_donation_can_be_edited(self):
        donation = make_donation(make_donor(), days_ago=3)
        donation.notes = "corrected note"
        donation.save()  # used to fail: the donation counted against its own 56-day interval

    def test_second_donation_within_56_days_is_rejected(self):
        donor = make_donor()
        make_donation(donor, days_ago=20)
        with self.assertRaises(ValidationError):
            make_donation(donor, days_ago=0)

    def test_low_hemoglobin_requires_deferral(self):
        with self.assertRaises(ValidationError):
            make_donation(make_donor(), hemoglobin_g_dl=Decimal("11.9"))


class DonationViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("staff", password="pass-1234-x")
        self.client.force_login(self.user)
        self.donation = make_donation(make_donor())

    def test_donation_list_renders_with_pending_unit(self):
        # used to crash: the template linked to a URL name that did not exist
        response = self.client.get(reverse("donation_list"))
        self.assertEqual(response.status_code, 200)

    def test_clear_unit_requires_post_and_clears(self):
        unit = self.donation.blood_unit
        self.assertEqual(self.client.get(reverse("clear_unit", args=[unit.pk])).status_code, 405)
        self.client.post(reverse("clear_unit", args=[unit.pk]))
        unit.refresh_from_db()
        self.assertEqual(unit.test_result, LabTestResult.CLEARED)

    def test_delete_donation_works_via_post_only(self):
        url = reverse("delete_donation", args=[self.donation.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)  # used to fail: `transaction` was not imported
        self.assertFalse(Donation.objects.filter(pk=self.donation.pk).exists())
        self.assertFalse(BloodUnit.objects.exists())


class AuthenticationTests(TestCase):
    def test_login_page_renders(self):
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)

    def test_dashboard_redirects_anonymous_users_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, f"{reverse('login')}?next=/")
