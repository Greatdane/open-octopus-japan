"""Tests for data models."""

from datetime import datetime

from open_octopus import (
    Account,
    Agreement,
    Consumption,
    LoyaltyPoints,
    PlannedDispatch,
    PostalArea,
    Product,
    Rate,
    SupplyPoint,
    Tariff,
)


def test_account():
    """Test Account model."""
    acc = Account(
        number="A-12345678",
        balance=-1500,
        name="田中太郎",
        status="ACTIVE",
        address="東京都渋谷区1-2-3"
    )
    assert acc.balance == -1500
    assert acc.number == "A-12345678"
    assert acc.name == "田中太郎"
    assert acc.status == "ACTIVE"
    assert acc.address == "東京都渋谷区1-2-3"


def test_consumption():
    """Test Consumption model with defaults."""
    c = Consumption(
        start=datetime(2024, 1, 1, 0, 0),
        end=datetime(2024, 1, 1, 0, 30),
        kwh=0.5
    )
    assert c.kwh == 0.5
    assert c.cost_estimate is None
    assert c.consumption_step is None
    assert c.consumption_rate_band is None


def test_consumption_with_cost():
    """Test Consumption model with cost estimate from API."""
    c = Consumption(
        start=datetime(2024, 1, 1, 0, 0),
        end=datetime(2024, 1, 1, 0, 30),
        kwh=0.5,
        cost_estimate=15.0,
        consumption_step=1,
        consumption_rate_band="standard"
    )
    assert c.cost_estimate == 15.0
    assert c.consumption_step == 1
    assert c.consumption_rate_band == "standard"


def test_tariff():
    """Test Tariff model."""
    t = Tariff(
        name="シンプルオクトパス",
        product_code="SIMPLE-2024",
        standing_charge=28.8,
        rates={"standard": 30.5, "base": 25.0, "fca": 3.5, "rel": 2.0},
        peak_rate=30.5
    )
    assert t.standing_charge == 28.8
    assert t.rates["standard"] == 30.5
    assert t.peak_rate == 30.5


def test_tariff_defaults():
    """Test Tariff with minimal fields."""
    t = Tariff(
        name="Test",
        product_code="TEST-1",
        standing_charge=0,
    )
    assert t.rates == {}
    assert t.peak_rate is None


def test_rate():
    """Test Rate model."""
    r = Rate(
        rate=30.5,
        period_end=datetime(2024, 1, 1, 23, 59, 59)
    )
    assert r.rate == 30.5


def test_supply_point():
    """Test SupplyPoint model."""
    sp = SupplyPoint(
        spin="SPIN123456",
        status="ACTIVE",
        meter_serial="M12345",
        agreements=[{
            "id": 1,
            "valid_from": "2024-01-01",
            "valid_to": None,
            "product_code": "SIMPLE-2024",
            "product_name": "シンプルオクトパス",
        }]
    )
    assert sp.spin == "SPIN123456"
    assert sp.meter_serial == "M12345"
    assert len(sp.agreements) == 1


def test_supply_point_defaults():
    """Test SupplyPoint with minimal fields."""
    sp = SupplyPoint(spin="SPIN123", status="ACTIVE")
    assert sp.meter_serial is None
    assert sp.agreements == []


def test_postal_area():
    """Test PostalArea model."""
    pa = PostalArea(
        postcode="916-0045",
        prefecture="福井県",
        city="鯖江市",
        area="宮前"
    )
    assert pa.postcode == "916-0045"
    assert pa.prefecture == "福井県"


def test_agreement():
    """Test Agreement model."""
    a = Agreement(
        id=42,
        valid_from=datetime(2024, 1, 1),
        valid_to=None,
        product_code="SIMPLE-2024",
        product_name="シンプルオクトパス",
    )
    assert a.id == 42
    assert a.valid_from == datetime(2024, 1, 1)
    assert a.valid_to is None
    assert a.product_code == "SIMPLE-2024"


def test_agreement_defaults():
    """Test Agreement with minimal fields."""
    a = Agreement(id=1)
    assert a.valid_from is None
    assert a.product_code == ""
    assert a.product_name == ""


def test_product():
    """Test Product model."""
    p = Product(
        code="GREEN-2024",
        display_name="グリーンオクトパス",
        description="100% renewable energy",
        standing_charge=28.8,
        rates={"standard": 25.0, "peak": 35.0},
    )
    assert p.code == "GREEN-2024"
    assert p.standing_charge == 28.8
    assert p.rates["standard"] == 25.0
    assert p.is_available is True


def test_product_defaults():
    """Test Product with minimal fields."""
    p = Product(code="TEST", display_name="Test")
    assert p.rates == {}
    assert p.standing_charge == 0.0
    assert p.description == ""


def test_loyalty_points():
    """Test LoyaltyPoints model."""
    lp = LoyaltyPoints(
        balance=500,
        ledger_entries=[{"value": 100, "reason": "SIGNUP"}],
    )
    assert lp.balance == 500
    assert len(lp.ledger_entries) == 1


def test_loyalty_points_defaults():
    """Test LoyaltyPoints with defaults."""
    lp = LoyaltyPoints()
    assert lp.balance == 0
    assert lp.ledger_entries == []


def test_planned_dispatch():
    """Test PlannedDispatch model."""
    d = PlannedDispatch(
        start=datetime(2024, 1, 1, 23, 0),
        end=datetime(2024, 1, 2, 5, 0),
        delta=2.5,
        source="smart-charge",
    )
    assert d.delta == 2.5
    assert d.source == "smart-charge"


def test_planned_dispatch_defaults():
    """Test PlannedDispatch with minimal fields."""
    d = PlannedDispatch(
        start=datetime(2024, 1, 1),
        end=datetime(2024, 1, 2),
    )
    assert d.delta is None
    assert d.source == ""
