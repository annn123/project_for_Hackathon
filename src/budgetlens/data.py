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
    for that department/category/month.
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
        df[column] = pd.to_numeric(original, errors="coerce")
        invalid = df[column].isna()
        if invalid.any():
            rows = (df.index[invalid] + 2).tolist()
            raise ValueError(f"{column} must be numeric; invalid CSV row(s): {rows}")
        if (df[column] < 0).any():
            rows = (df.index[df[column] < 0] + 2).tolist()
            raise ValueError(f"{column} cannot be negative; invalid CSV row(s): {rows}")

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
    grouped = df.groupby("department", sort=True, as_index=False).agg(
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
