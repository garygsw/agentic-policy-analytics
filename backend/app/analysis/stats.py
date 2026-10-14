"""Statistics on tidy tables. The LLM never calculates a number: it chooses what to compute, these functions
compute it, and every result becomes a numbered fact the report must cite."""
import re

import numpy as np
import pandas as pd

from app.data.registry import CuratedDataset

VALUE_COLUMNS = ("period", "measure", "value", "unit")


class AnalysisError(ValueError):
    """A request that cannot be computed as asked. The message says how to fix it, and goes back to the agent."""


def dimensions(frame: pd.DataFrame) -> list[str]:
    return [c for c in frame.columns if c not in VALUE_COLUMNS]


def _period_key(period: str) -> str:
    return period.removeprefix("FY")


def _in_range(periods: pd.Series, start: str | None, end: str | None) -> pd.Series:
    """Compare on the period prefix, so end="2024" keeps 2024-Q4 and start="2020" keeps 2020-01."""
    keys = periods.map(_period_key)
    keep = pd.Series(True, index=periods.index)
    if start:
        keep &= keys.str[:len(str(start))] >= str(start)
    if end:
        keep &= keys.str[:len(str(end))] <= str(end)
    return keep


def select_series(frame: pd.DataFrame, dataset: CuratedDataset, measure: str, filters: dict | None = None,
                  start: str | None = None, end: str | None = None) -> tuple[pd.Series, str, list[str]]:
    """One value per period for `measure`. Dimensions the filters leave open are added up, but only where the
    dataset says that measure can be summed across them. Returns (series, unit, notes)."""
    if measure not in set(frame["measure"]):
        raise AnalysisError(f"unknown measure '{measure}'; measures: {sorted(set(frame['measure']))}")
    rows = frame[frame["measure"] == measure]
    dims = dimensions(frame)
    for dim, value in (filters or {}).items():
        if dim not in dims:
            raise AnalysisError(f"unknown dimension '{dim}'; dimensions: {dims}")
        matched = rows[rows[dim].str.lower() == str(value).strip().lower()]
        if matched.empty:
            raise AnalysisError(f"no rows where {dim} = '{value}'; values include: {sorted(rows[dim].unique())[:25]}")
        rows = matched
    rows = rows[_in_range(rows["period"], start, end)]
    if rows.empty:
        raise AnalysisError(f"no {measure} data between {start or 'start'} and {end or 'end'}; coverage: "
                            f"{frame['period'].min()} to {frame['period'].max()}")
    summed = []
    for dim in dims:
        values = rows[dim].unique()
        if dim in (filters or {}) or len(values) <= 1:
            continue
        if dim not in dataset.summable.get(measure, ()):
            raise AnalysisError(f"'{dim}' has {len(values)} values and {measure} cannot be added across it; "
                                f"filter {dim} to one of: {sorted(values)[:25]}")
        summed.append(dim)
    series = rows.groupby("period")["value"].sum().sort_index(key=lambda p: p.map(_period_key))
    return series, rows["unit"].iloc[0], ([f"added up across {', '.join(summed)}"] if summed else [])


def is_yearly(series: pd.Series) -> bool:
    return all(re.fullmatch(r"\d{4}", _period_key(p)) for p in series.index)


def year_on_year(series: pd.Series) -> pd.Series:
    """Percentage change against the same period one year earlier."""
    if is_yearly(series):
        lag = 1
    else:
        lag = 4 if "-Q" in series.index[0] else 12
    return (series / series.shift(lag) - 1) * 100


def trend(series: pd.Series) -> dict:
    first_period, last_period = series.index[0], series.index[-1]
    first, last = float(series.iloc[0]), float(series.iloc[-1])
    result = {"start": (first_period, first), "end": (last_period, last), "change": last - first,
              "change_pct": (last / first - 1) * 100 if first else None,
              "peak": (series.idxmax(), float(series.max())), "low": (series.idxmin(), float(series.min())),
              "cagr_pct": None}
    if is_yearly(series):
        years = int(_period_key(last_period)) - int(_period_key(first_period))
        if years > 0 and first > 0 and last > 0:
            result["cagr_pct"] = ((last / first) ** (1 / years) - 1) * 100
    yoy = year_on_year(series).dropna()
    result["latest_yoy"] = (yoy.index[-1], float(yoy.iloc[-1])) if not yoy.empty else None
    return result


def to_annual(series: pd.Series, summable: bool) -> pd.Series:
    """Quarterly/monthly -> annual: totals for countable measures, averages otherwise. Incomplete years dropped."""
    if is_yearly(series):
        return series
    years = series.index.map(lambda p: _period_key(p)[:4])
    per_year = 4 if "-Q" in series.index[0] else 12
    counts = series.groupby(years).size()
    annual = series.groupby(years).sum() if summable else series.groupby(years).mean()
    return annual[counts == per_year]


def _pearson(x: pd.Series, y: pd.Series) -> float | None:
    with np.errstate(divide="ignore", invalid="ignore"):
        r = x.corr(y)
    return None if pd.isna(r) else float(r)  # undefined when a series is constant


def correlation(a: pd.Series, b: pd.Series) -> dict:
    """Pearson correlation of levels and of changes between consecutive periods, over the periods both series
    cover. Two series that both grow over time correlate strongly whether or not they are related; the
    correlation of their changes is the more telling number. Changes are differences, not percentages, so a
    zero value (e.g. nil retrenchments) cannot produce an infinite change."""
    both = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(both) < 4:
        raise AnalysisError(f"only {len(both)} overlapping periods; need at least 4 for a correlation")
    result = {"n": len(both), "from": both.index[0], "to": both.index[-1],
              "r_levels": _pearson(both.iloc[:, 0], both.iloc[:, 1]), "r_changes": None}
    changes = both.diff().dropna()
    if len(changes) >= 4:
        result["r_changes"] = _pearson(changes.iloc[:, 0], changes.iloc[:, 1])
    return result
