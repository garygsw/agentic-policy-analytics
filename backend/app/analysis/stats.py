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


def period_key(period: str) -> str:
    """Sort key for periods ("FY2020" sorts with "2020")."""
    return _period_key(period)


def _in_range(periods: pd.Series, start: str | None, end: str | None) -> pd.Series:
    """Compare on the period prefix, so end="2024" keeps 2024-Q4 and start="2020" keeps 2020-01."""
    keys = periods.map(_period_key)
    keep = pd.Series(True, index=periods.index)
    if start:
        keep &= keys.str[:len(str(start))] >= str(start)
    if end:
        keep &= keys.str[:len(str(end))] <= str(end)
    return keep


def matching_rows(frame: pd.DataFrame, measure: str, filters: dict | None = None, start: str | None = None,
                  end: str | None = None) -> pd.DataFrame:
    """The rows of `measure` that match the filters (exact values, any letter case) and the period range."""
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
    return rows


def select_series(frame: pd.DataFrame, dataset: CuratedDataset, measure: str, filters: dict | None = None,
                  start: str | None = None, end: str | None = None) -> tuple[pd.Series, str, list[str]]:
    """One value per period for `measure`. Dimensions the filters leave open are added up, but only where the
    dataset says that measure can be summed across them. Returns (series, unit, notes)."""
    rows = matching_rows(frame, measure, filters, start, end)
    dims = dimensions(frame)
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


def to_annual(series: pd.Series, rule: str) -> pd.Series:
    """Quarterly/monthly -> annual, each year's periods combined by the measure's rule ("sum", "mean" or "max").
    Incomplete years dropped."""
    if is_yearly(series):
        return series
    years = series.index.map(lambda p: _period_key(p)[:4])
    per_year = 4 if "-Q" in series.index[0] else 12
    counts = series.groupby(years).size()
    annual = series.groupby(years).agg(rule)
    return annual[counts == per_year]


MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
RULE_WORDS = {"sum": "total", "mean": "average", "max": "highest"}


def months_label(months: list[int]) -> str:
    """[7, 8, 9, 10] -> "Jul-Oct"; months that do not run on are listed."""
    if len(months) > 1 and months == list(range(months[0], months[-1] + 1)):
        return f"{MONTH_NAMES[months[0] - 1]}-{MONTH_NAMES[months[-1] - 1]}"
    return ", ".join(MONTH_NAMES[m - 1] for m in months)


def season(frame: pd.DataFrame, months: list[int], rule) -> pd.DataFrame:
    """The chosen calendar months of each year, combined into one row per year for each measure and combination of
    dimension values, by rule(measure): "sum", "mean" or "max". The periods become years. A year missing any of
    the months is left out, so a season still in progress is not compared with whole ones."""
    month = frame["period"].str.extract(r"^\d{4}-(\d{2})$", expand=False)
    if month.isna().any():
        raise AnalysisError(f"months only applies to monthly data; this dataset's periods look like "
                            f"'{frame['period'].iloc[0]}'")
    rows = frame[month.astype(int).isin(months)].assign(period=lambda f: f["period"].str[:4])
    keys = ["period", "unit", *dimensions(frame)]
    parts = []
    for measure, part in rows.groupby("measure"):
        values = part.groupby(keys, dropna=False)["value"]
        combined = values.agg(rule(measure))[values.size() == len(months)]
        parts.append(combined.reset_index().assign(measure=measure))
    if not parts:
        raise AnalysisError(f"no values in {months_label(months)}")
    return pd.concat(parts, ignore_index=True)[list(frame.columns)]


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


def observations(frame: pd.DataFrame, measure: str, filters: dict | None = None, group_by: str | None = None,
                 start: str | None = None, end: str | None = None) -> tuple[dict[str, pd.Series], str, list[str]]:
    """Every value of `measure`, by group, with nothing added up: what a distribution is drawn from. Each period,
    and each value of a dimension the filters leave open, is one observation. Returns (values by group, unit,
    the dimensions pooled within each group)."""
    rows = matching_rows(frame, measure, filters, start, end)
    dims = dimensions(frame)
    if group_by and group_by not in dims:
        raise AnalysisError(f"unknown dimension '{group_by}'; dimensions: {dims}")
    pooled = [d for d in dims if d != group_by and d not in (filters or {}) and rows[d].nunique() > 1]
    rows = rows.dropna(subset=["value"])
    groups = {str(k): g["value"] for k, g in rows.groupby(group_by)} if group_by else {"all": rows["value"]}
    return groups, rows["unit"].iloc[0], pooled


def distribution(values: pd.Series) -> dict:
    """Quartiles, the range, and box-plot whiskers (the furthest values within 1.5 IQR of the quartiles)."""
    q1, median, q3 = (float(v) for v in values.quantile([0.25, 0.5, 0.75]))
    reach = 1.5 * (q3 - q1)
    inside = values[(values >= q1 - reach) & (values <= q3 + reach)]
    return {"n": len(values), "min": float(values.min()), "q1": q1, "median": median, "q3": q3,
            "max": float(values.max()), "lower_whisker": float(inside.min()), "upper_whisker": float(inside.max()),
            "outliers": len(values) - len(inside)}

