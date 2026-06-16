"""Tests for the deterministic price calculator (the 'measured' core)."""

from bot.pricing import PriceBook


def book():
    return PriceBook()


def test_duration_bracket_selection():
    b = book()
    # Camry HYBRID XV70: 70/60/55/45 across 1-3 / 4-9 / 10-25 / 26-89 (sample data)
    q = "Toyota Camry HYBRID (XV70)"
    assert b.quote(q, 2).price_per_day == 70    # 1-3
    assert b.quote(q, 5).price_per_day == 60    # 4-9
    assert b.quote(q, 15).price_per_day == 55   # 10-25
    assert b.quote(q, 40).price_per_day == 45   # 26-89
    assert b.quote(q, 500).price_per_day == 45  # clamps to cheapest


def test_total_and_deposit():
    q = book().quote("Renault Logan II", 2)
    assert q.price_per_day == 35 and q.total == 70 and q.deposit == 200


def test_cyrillic_alias_match():
    b = book()
    assert b.find_car("камрі гібрид").model == "Toyota Camry HYBRID (XV70)"
    assert b.find_car("ауді").model == "Audi A4 B9"
    assert b.find_car("логан").model == "Renault Logan II"


def test_digit_does_not_contaminate_match():
    # '5 днів' must NOT match Infiniti Q50 via the '5' inside 'Q50'
    car = book().find_car("камрі гібрид на 5 днів")
    assert "Camry" in car.model and "Q50" not in car.model


def test_find_matches_disambiguation():
    matches = book().find_matches("БМВ")
    assert len(matches) > 1
    assert all("BMW" in c.model for c in matches)


def test_unknown_car_returns_none():
    assert book().find_car("ламборгіні діабло") is None
    assert book().quote("ракета", 3) is None


def test_days_must_be_positive():
    import pytest
    with pytest.raises(ValueError):
        book().quote("Renault Logan II", 0)
