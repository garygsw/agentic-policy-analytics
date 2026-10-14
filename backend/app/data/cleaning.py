"""Generic cleaning into one tidy shape, with a data-quality report.

Every curated dataset is turned into rows of:
    period | <dimension columns> | measure | value | unit
so the analytics tools can work on any dataset. Rows that cannot be used are dropped, and every drop is
counted in the QualityReport rather than silently discarded.
"""
import re
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

Frequency = Literal["annual", "quarterly", "monthly", "financial_year", "none"]

# Placeholders official tables use for "no value". They become missing values, not parse errors.
MISSING_MARKERS = {"", "na", "n.a.", "n/a", "-", "--", "...", "nil", "null", "none", "x", "s", "(x)", "nan"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"], 1)}


class QualityReport(BaseModel):
    dataset_id: str
    rows_in: int = 0
    rows_out: int = 0
    invalid_periods: int = 0
    missing_values: dict[str, int] = Field(default_factory=dict)      # per measure: placeholders/blanks
    unparseable_values: dict[str, int] = Field(default_factory=dict)  # per measure: text that is not a number
    duplicates_removed: int = 0
    total_rows_removed: int = 0  # "Total"/aggregate rows dropped to avoid double counting
    personal_data_columns_dropped: list[str] = Field(default_factory=list)
    coverage: tuple[str, str] | None = None
    notes: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.rows_out:,} clean rows from {self.rows_in:,}"]
        if self.coverage:
            parts.append(f"covering {self.coverage[0]} to {self.coverage[1]}")
        for label, value in [("invalid periods", self.invalid_periods),
                             ("missing values", sum(self.missing_values.values())),
                             ("unparseable values", sum(self.unparseable_values.values())),
                             ("duplicates removed", self.duplicates_removed),
                             ("total rows removed", self.total_rows_removed)]:
            if value:
                parts.append(f"{value:,} {label}")
        if self.personal_data_columns_dropped:
            parts.append("personal data columns dropped: " + ", ".join(self.personal_data_columns_dropped))
        return "; ".join(parts + self.notes)


def to_number(values: pd.Series, zero_markers: tuple[str, ...] = ()) -> tuple[pd.Series, int, int]:
    """Parse numbers like "1,234", "$5.6", "12%". Returns (numbers, missing placeholders, unparseable).

    zero_markers: placeholders that mean nil rather than unknown. In MOM tables "-" means "nil or
    negligible", so it must become 0, not a gap."""
    text = values.astype("string").str.strip()
    text = text.mask(text.isin(zero_markers), "0")
    missing = text.isna() | text.str.lower().isin(MISSING_MARKERS)
    cleaned = text.str.replace(r"[,$%\s]", "", regex=True)
    numbers = pd.to_numeric(cleaned.where(~missing), errors="coerce")
    unparseable = int((numbers.isna() & ~missing).sum())
    return numbers.astype("float64"), int(missing.sum()), unparseable


def normalise_period(value, frequency: Frequency) -> str | None:
    """One spelling per frequency: "2024", "2024-Q1", "2024-03", "FY2024". None if unreadable."""
    text = str(value).strip()
    if frequency == "none":
        return ""
    if frequency == "annual":
        match = re.fullmatch(r"(\d{4})(?:\.0)?(?:-\d{2}-\d{2})?", text)
        return match.group(1) if match else None
    if frequency == "financial_year":
        match = re.search(r"(\d{4})", text)
        return f"FY{match.group(1)}" if match else None
    if frequency == "quarterly":
        for pattern, year_group, quarter_group in [(r"(\d{4})\s*-?\s*Q([1-4])", 1, 2), (r"(\d{4})\s+([1-4])Q", 1, 2),
                                                   (r"([1-4])Q\s*(\d{4})", 2, 1)]:
            match = re.fullmatch(pattern, text, re.I)
            if match:
                return f"{match.group(year_group)}-Q{match.group(quarter_group)}"
        return None
    match = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", text)  # monthly: 2024-03 or 2024-03-01
    if match and 1 <= int(match.group(2)) <= 12:
        return f"{match.group(1)}-{int(match.group(2)):02d}"
    match = re.fullmatch(r"([A-Za-z]{3})[a-z]*[\s-]+(\d{4})", text)  # Mar 2024, MAR-2024
    if match and match.group(1).lower() in MONTHS:
        return f"{match.group(2)}-{MONTHS[match.group(1).lower()]:02d}"
    return None


def tidy(df: pd.DataFrame, dataset_id: str, *, period: str | None, frequency: Frequency, dims: list[str],
         measures: dict[str, str], total_labels: tuple[str, ...] = (),
         zero_markers: tuple[str, ...] = ()) -> tuple[pd.DataFrame, QualityReport]:
    """Reshape a wide official table into the tidy shape and report every row it had to drop.

    measures: {column: unit}. total_labels: dimension values such as "Total" that aggregate other rows; they
    are removed so that sums over a dimension do not double count.
    """
    report = QualityReport(dataset_id=dataset_id, rows_in=len(df))
    frame = df.copy()
    for dim in dims:
        frame[dim] = frame[dim].astype("string").str.strip()
    if total_labels:
        is_total = pd.Series(False, index=frame.index)
        for dim in dims:
            is_total |= frame[dim].str.lower().isin([t.lower() for t in total_labels])
        report.total_rows_removed = int(is_total.sum())
        frame = frame[~is_total]
    if period:
        frame["period"] = [normalise_period(v, frequency) for v in frame[period]]
        report.invalid_periods = int(frame["period"].isna().sum())
        frame = frame[frame["period"].notna()]
    else:
        frame["period"] = ""
    before = len(frame)
    frame = frame.drop_duplicates(subset=["period", *dims, *measures])  # counted in source rows, before reshaping
    report.duplicates_removed = before - len(frame)
    long_parts = []
    for column, unit in measures.items():
        numbers, missing, unparseable = to_number(frame[column], zero_markers)
        report.missing_values[column] = missing
        report.unparseable_values[column] = unparseable
        part = frame[["period", *dims]].assign(measure=column, value=numbers, unit=unit)
        long_parts.append(part[part["value"].notna()])
    result = pd.concat(long_parts, ignore_index=True)
    result = result.sort_values(["measure", *dims, "period"]).reset_index(drop=True)
    report.rows_out = len(result)
    periods = sorted(p for p in result["period"].unique() if p)
    report.coverage = (periods[0], periods[-1]) if periods else None
    return result, report
