"""Run-rate forecast and What-If budget calculations."""

from __future__ import annotations

from math import isfinite, sqrt

import pandas as pd
from scipy.stats import t


def forecast_department(
    monthly: pd.DataFrame,
    annual_budget_override: float | None = None,
) -> dict[str, object]:
    """Forecast a department's annual spend from its monthly observations.

    Complete year-to-date coverage uses actual YTD spend plus a run-rate
    estimate for the remaining months. Sparse/incomplete coverage uses the
    observed monthly average annualized to 12 months. The interval and
    exceedance probability use a Student-t predictive approximation.
    """
    if monthly.empty:
        raise ValueError("monthly data is required for forecasting")

    monthly = monthly.sort_values("period").reset_index(drop=True)
    actuals = monthly["actual"].astype(float)
    budgets = monthly["budget"].astype(float)
    if not actuals.map(isfinite).all() or not budgets.map(isfinite).all():
        raise ValueError("monthly actual and budget values must be finite")

    n = len(monthly)
    mean_actual = float(actuals.mean())
    mean_budget = float(budgets.mean())
    if annual_budget_override is None:
        annual_budget = mean_budget * 12.0
    else:
        annual_budget = float(annual_budget_override)
        if not isfinite(annual_budget) or annual_budget <= 0:
            raise ValueError("annual_budget_override must be finite and greater than zero")

    latest_month = int(str(monthly.iloc[-1]["period"]).split("-")[1])
    observed_month_numbers = [int(str(period).split("-")[1]) for period in monthly["period"]]
    has_complete_ytd = observed_month_numbers == list(range(1, latest_month + 1))

    if has_complete_ytd:
        remaining_months = 12 - latest_month
        year_end_amount = float(actuals.sum() + remaining_months * mean_actual)
        method = "ytd_plus_remaining_run_rate"
    else:
        remaining_months = None
        year_end_amount = mean_actual * 12.0
        method = "annualized_observed_months"

    if n >= 2:
        sample_std = float(actuals.std(ddof=1))
        if has_complete_ytd:
            forecast_scale = sample_std * sqrt(
                remaining_months * (1.0 + remaining_months / n)
            ) if remaining_months else 0.0
        else:
            forecast_scale = sample_std * 12.0 / sqrt(n)

        critical = float(t.ppf(0.975, df=n - 1))
        margin = critical * forecast_scale
        ci95: dict[str, float] | None = {
            "lower": max(0.0, year_end_amount - margin),
            "upper": year_end_amount + margin,
        }
        if forecast_scale > 0:
            overrun_probability: float | None = float(
                t.sf((annual_budget - year_end_amount) / forecast_scale, df=n - 1)
            )
        else:
            overrun_probability = (
                1.0 if year_end_amount > annual_budget
                else 0.0 if year_end_amount < annual_budget
                else 0.5
            )
    else:
        # One monthly point cannot estimate spending variance.
        sample_std = None
        ci95 = None
        overrun_probability = None

    return {
        "year_end_amount": year_end_amount,
        "ci95": ci95,
        "overrun_probability": overrun_probability,
        "annual_budget": annual_budget,
        "method": method,
        "periods_used": n,
        "monthly_mean_actual": mean_actual,
        "monthly_sample_std": sample_std,
    }
