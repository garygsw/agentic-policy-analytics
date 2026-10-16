"""Numbers as people read them, the same in every table and chart image of a report (HTML, Markdown and PDF)."""
import math
from numbers import Real


def readable(value) -> str:
    """Thousands separators and no needless decimals: 69563.0 -> "69,563", 602.60 -> "602.6", 1234.567 ->
    "1,234.57". Numbers below 0.01 keep two significant digits instead of rounding to nothing: 0.00421 -> "0.0042".
    Text (a heatmap's column name, for example) is returned as it is; a missing value is empty."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, bool) or not isinstance(value, Real):
        return str(value)
    if float(value).is_integer():
        return f"{float(value) + 0.0:,.0f}"  # + 0.0 turns -0.0 into 0.0
    decimals = 2 if abs(value) >= 0.01 else 1 - math.floor(math.log10(abs(value)))
    return f"{value:,.{decimals}f}".rstrip("0").rstrip(".")


def tick(value, _position=None) -> str:
    """For matplotlib's FuncFormatter: an axis or colour bar label."""
    return readable(value)
