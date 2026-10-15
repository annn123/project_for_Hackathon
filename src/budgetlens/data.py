"""CSV loading, validation, cleaning, and budget-use calculations."""

from __future__ import annotations

from pathlib import Path
from typing import IO

import pandas as pd

REQUIRED_COLUMNS = {"department", "period", "budget", "actual"}
OPTIONAL_COLUMNS = {"category"}
CANONICAL_COLUMNS = ["department", "period", "category", "budget", "actual"]


def prepare_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize the agreed CSV columns.

    ``period`` must be a month in YYYY-MM form. Budget and actual are
    non-negative amounts in the same currency; budget is the planned amount
    for that department/category/month. A blank actual is allowed only for
    whole, trailing department-months that have not happened or been reported.
    """
    df = frame.copy()
    df.columns = [str(column).strip().lower() for column in df.columns]
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required CSV column(s): {', '.join(sorted(missing))}")

    if "category" not in df:
        df["category"] = "Uncategorized"
    df = df[CANONICAL_COLUMNS]

    for column in ("department", "category"):
        df[column] = df[column].fillna("").astype(str).str.strip()
    if (df["department"] == "").any():
        raise ValueError("department cannot be blank")
    df.loc[df["category"] == "", "category"] = "Uncategorized"

    period = df["period"].astype("string").str.strip()
    parsed_period = pd.to_datetime(period + "-01", format="%Y-%m-%d", errors="coerce")
    invalid_period = parsed_period.isna()
    if invalid_period.any():
        rows = (df.index[invalid_period] + 2).tolist()
        raise ValueError(f"period must use YYYY-MM format; invalid CSV row(s): {rows}")
    df["period"] = parsed_period.dt.strftime("%Y-%m")

    for column in ("budget", "actual"):
        original = df[column]
        blank = original.isna() | original.astype("string").str.strip().eq("").fillna(False)
        df[column] = pd.to_numeric(original, errors="coerce")
        invalid = df[column].isna() & (~blank if column == "actual" else True)
        if invalid.any():
            rows = (df.index[invalid] + 2).tolist()
            raise ValueError(f"{column} must be numeric; invalid CSV row(s): {rows}")
        if (df[column] < 0).any():
            rows = (df.index[df[column] < 0] + 2).tolist()
            raise ValueError(f"{column} cannot be negative; invalid CSV row(s): {rows}")

    month_actuals = df.groupby(["department", "period"], sort=True)["actual"].agg(
        has_actual="count", row_count="size"
    )
    partial_months = month_actuals[
        (month_actuals["has_actual"] > 0) & (month_actuals["has_actual"] < month_actuals["row_count"])
    ]
    if not partial_months.empty:
        raise ValueError("actual must be filled for every category in a month, or blank for the whole month")

    # Missing actuals represent only future/unreported trailing months. Do not
    # silently annualize a file with a missing month followed by later actuals.
    for department, rows in df.groupby("department", sort=False):
        month_state = rows.groupby("period", sort=True)["actual"].count()
        missing_seen = False
        for period, actual_count in month_state.items():
            if actual_count == 0:
                missing_seen = True
            elif missing_seen:
                raise ValueError(
                    f"actual values must not resume after a blank future month; department: {department}, period: {period}"
                )
        if not month_state.gt(0).any():
            raise ValueError(f"at least one actual month is required for department: {department}")

    # Stable order makes API responses and charts deterministic.
    return df.sort_values(["department", "period", "category"], kind="stable").reset_index(drop=True)


def load_and_clean_csv(source: str | Path | IO[str]) -> pd.DataFrame:
    """Read a UTF-8 CSV path or text stream, then validate and normalize it."""
    try:
        frame = pd.read_csv(source)
    except pd.errors.EmptyDataError as exc:
        raise ValueError("CSV is empty") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must be encoded as UTF-8") from exc
    if frame.empty:
        raise ValueError("CSV contains a header but no data rows")
    return prepare_dataframe(frame)


def compute_department_metrics(frame: pd.DataFrame) -> list[dict[str, object]]:
    """Aggregate cleaned rows into department-level budget-use metrics.

    The usage rate is actual / budget. The variance percentage is
    (actual - budget) / budget. Both are ``None`` when total budget is zero.
    """
    df = prepare_dataframe(frame)
    observed = df.loc[df["actual"].notna()].copy()
    grouped = observed.groupby("department", sort=True, as_index=False).agg(
        budget=("budget", "sum"),
        actual=("actual", "sum"),
        period_count=("period", "nunique"),
        latest_period=("period", "max"),
    )
    results: list[dict[str, object]] = []
    for row in grouped.to_dict(orient="records"):
        budget = float(row["budget"])
        actual = float(row["actual"])
        variance = actual - budget
        ratio = actual / budget if budget > 0 else None
        results.append({
            "department": row["department"],
            "budget": budget,
            "actual": actual,
            "variance": variance,
            "usage_rate": ratio,
            "variance_pct": variance / budget if budget > 0 else None,
            "period_count": int(row["period_count"]),
            "latest_period": row["latest_period"],
        })
    return results
