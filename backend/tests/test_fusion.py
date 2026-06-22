import pytest


def test_drought_index_all_healthy():
    """All healthy values should give drought_index near 1.0."""
    def normalize(val, min_v, max_v):
        if val is None: return 0.5
        return max(0.0, min(1.0, (val - min_v) / (max_v - min_v)))

    def calc(gws, rain_anom, ndvi):
        return 0.4 * normalize(gws, -5, 5) + 0.3 * normalize(rain_anom, -200, 200) + 0.3 * normalize(ndvi, 0, 0.8)

    assert calc(5, 200, 0.8) > 0.9


def test_drought_index_all_critical():
    """All critical values should give drought_index near 0.0."""
    def normalize(val, min_v, max_v):
        if val is None: return 0.5
        return max(0.0, min(1.0, (val - min_v) / (max_v - min_v)))

    def calc(gws, rain_anom, ndvi):
        return 0.4 * normalize(gws, -5, 5) + 0.3 * normalize(rain_anom, -200, 200) + 0.3 * normalize(ndvi, 0, 0.8)

    assert calc(-5, -200, 0.0) < 0.1


def test_drought_index_mixed():
    """Mixed values should give drought_index around 0.5."""
    def normalize(val, min_v, max_v):
        if val is None: return 0.5
        return max(0.0, min(1.0, (val - min_v) / (max_v - min_v)))

    def calc(gws, rain_anom, ndvi):
        return 0.4 * normalize(gws, -5, 5) + 0.3 * normalize(rain_anom, -200, 200) + 0.3 * normalize(ndvi, 0, 0.8)

    di = calc(0, 0, 0.4)
    assert 0.4 < di < 0.6


def test_drought_index_none_handling():
    """None values should default to 0.5 (middle)."""
    def normalize(val, min_v, max_v):
        if val is None: return 0.5
        return max(0.0, min(1.0, (val - min_v) / (max_v - min_v)))

    def calc(gws, rain_anom, ndvi):
        return 0.4 * normalize(gws, -5, 5) + 0.3 * normalize(rain_anom, -200, 200) + 0.3 * normalize(ndvi, 0, 0.8)

    di = calc(None, None, None)
    assert di == 0.5


def test_risk_level_classification():
    """Test risk level thresholds."""
    def risk(di):
        if di is None: return "tidak_ada_data"
        if di >= 0.6: return "normal"
        if di >= 0.4: return "waspada"
        if di >= 0.2: return "kritis"
        return "sangat_kritis"

    assert risk(0.7) == "normal"
    assert risk(0.5) == "waspada"
    assert risk(0.3) == "kritis"
    assert risk(0.1) == "sangat_kritis"
    assert risk(None) == "tidak_ada_data"


def test_normalize_clamping():
    """Normalize should clamp to 0..1."""
    def normalize(val, min_v, max_v):
        if val is None: return 0.5
        return max(0.0, min(1.0, (val - min_v) / (max_v - min_v)))

    assert normalize(10, 0, 5) == 1.0  # above max
    assert normalize(-10, 0, 5) == 0.0  # below min
    assert normalize(2.5, 0, 5) == 0.5  # midpoint
