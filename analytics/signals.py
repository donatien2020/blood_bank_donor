"""Refresh predictions automatically whenever new data arrives.

A new donation, blood request, issued unit or stock change (from the web
forms, the admin or the ingest API) schedules a refresh that runs once the
database transaction commits. Several changes in one transaction trigger a
single refresh.
"""

import logging
import threading
from contextlib import contextmanager

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from donors.models import BloodUnit, Donation
from inventory.models import BloodRequest, IssuedUnit

logger = logging.getLogger(__name__)
_state = threading.local()


@contextmanager
def predictions_paused():
    """Skip automatic refreshes, e.g. while loading thousands of rows."""
    previous = getattr(_state, "paused", False)
    _state.paused = True
    try:
        yield
    finally:
        _state.paused = previous


def _schedule(key, func, *args):
    if getattr(_state, "paused", False):
        return
    if not transaction.get_connection().in_atomic_block:
        _safe_call(func, args)
        return
    pending = getattr(_state, "pending", None)
    if pending is None:
        pending = _state.pending = {}
    pending[key] = (func, args)
    # Registered every time; the first callback runs all jobs, later ones find
    # nothing left to do.
    transaction.on_commit(_run_pending)


def _safe_call(func, args):
    try:
        func(*args)
    except Exception:  # predictions must never break data entry
        logger.exception("Prediction refresh failed")


def _run_pending():
    pending = getattr(_state, "pending", None) or {}
    _state.pending = None
    for func, args in pending.values():
        _safe_call(func, args)


def _refresh_demand():
    from .services import refresh_demand_forecasts
    refresh_demand_forecasts()


def _refresh_donor(donor_id):
    from donors.models import Donor
    from .services import refresh_donor_prediction
    donor = Donor.objects.filter(pk=donor_id).first()
    if donor is not None:
        refresh_donor_prediction(donor)


@receiver([post_save, post_delete], sender=Donation)
def donation_changed(sender, instance, **kwargs):
    _schedule(("donor", instance.donor_id), _refresh_donor, instance.donor_id)
    _schedule("demand", _refresh_demand)


@receiver([post_save, post_delete], sender=BloodRequest)
@receiver([post_save, post_delete], sender=IssuedUnit)
@receiver([post_save, post_delete], sender=BloodUnit)
def stock_or_demand_changed(sender, instance, **kwargs):
    _schedule("demand", _refresh_demand)
