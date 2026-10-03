"""
Tests for Epic 6's record_kind interface refinement (Q2). Pure interface
contract tests — no Normalization Engine exists yet, and this slice
deliberately doesn't build one. SynchronizationEngine does not consume
record_kind yet; these tests only prove the contract itself is correct.
"""

from __future__ import annotations

from trainiq.connectors.base import CapabilityTier, Connector, RecordKind
from trainiq.connectors.eufy import EufyConnector
from trainiq.connectors.peloton import PelotonConnector
from trainiq.connectors.strava import StravaConnector
from trainiq.credentials.store import CredentialStore


class _BareMockConnector(Connector):
    """The minimal possible connector — implements only what the abstract
    base actually requires, declares nothing about record_kind at all.
    Its whole purpose here is to prove the DEFAULT applies without any
    connector author needing to think about it."""

    capability_tier = CapabilityTier.TIER_1_OFFICIAL

    def authenticate(self) -> bool:
        return True

    def download(self, since: str | None = None) -> list[dict]:
        return []

    def normalize(self, raw: dict) -> dict:
        return raw


def test_default_record_kind_is_activity():
    connector = _BareMockConnector("bare")
    assert connector.record_kind == RecordKind.ACTIVITY


def test_strava_connector_uses_default_activity_record_kind():
    """Strava never declares record_kind explicitly — proves the default
    is sufficient, per the "zero changes needed" requirement."""
    store = CredentialStore()
    connector = StravaConnector(store, stravalib_client=object())
    assert connector.record_kind == RecordKind.ACTIVITY


def test_peloton_connector_uses_default_activity_record_kind():
    store = CredentialStore()
    connector = PelotonConnector(store, session=object())
    assert connector.record_kind == RecordKind.ACTIVITY


def test_eufy_connector_overrides_to_weigh_in():
    store = CredentialStore()
    connector = EufyConnector(store, device_id="dev-1", session=object())
    assert connector.record_kind == RecordKind.WEIGH_IN


def test_record_kind_is_a_class_attribute_not_requiring_instantiation_logic():
    """Confirms this is a static, declarative property of the connector
    TYPE — not something computed per-instance or requiring any method
    call — consistent with how capability_tier already works."""
    assert StravaConnector.record_kind == RecordKind.ACTIVITY
    assert EufyConnector.record_kind == RecordKind.WEIGH_IN
