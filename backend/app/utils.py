from datetime import date
from typing import Optional


def classify_ndvi(ndvi_value: float, title_case: bool = False) -> str:
    if ndvi_value >= 0.5:
        return "Vegetasi Lebat" if title_case else "lebat"
    if ndvi_value >= 0.3:
        return "Vegetasi Sedang" if title_case else "sedang"
    if ndvi_value >= 0.1:
        return "Vegetasi Jarang" if title_case else "jarang"
    return "Lahan Kritis" if title_case else "kritis"


def ndvi_color(kondisi: str) -> str:
    return {
        "lebat": "#1D9E75",
        "sedang": "#639922",
        "jarang": "#BA7517",
        "kritis": "#E24B4A"
    }.get(kondisi, "#888780")


def format_period_label(period_value: Optional[date]) -> Optional[str]:
    if not period_value:
        return None
    return period_value.strftime("%Y-%m")
