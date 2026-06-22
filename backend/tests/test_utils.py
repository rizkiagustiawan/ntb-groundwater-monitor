from datetime import date
from app.utils import classify_ndvi, ndvi_color, format_period_label


def test_classify_ndvi():
    assert classify_ndvi(0.6) == "lebat"
    assert classify_ndvi(0.35) == "sedang"
    assert classify_ndvi(0.15) == "jarang"
    assert classify_ndvi(0.05) == "kritis"


def test_classify_ndvi_title_case():
    assert classify_ndvi(0.6, title_case=True) == "Vegetasi Lebat"
    assert classify_ndvi(0.05, title_case=True) == "Lahan Kritis"


def test_ndvi_color():
    assert ndvi_color("lebat") == "#1D9E75"
    assert ndvi_color("sedang") == "#639922"
    assert ndvi_color("jarang") == "#BA7517"
    assert ndvi_color("kritis") == "#E24B4A"
    assert ndvi_color("unknown") == "#888780"


def test_format_period_label():
    assert format_period_label(date(2024, 1, 1)) == "2024-01"
    assert format_period_label(date(2023, 12, 1)) == "2023-12"
    assert format_period_label(None) is None
