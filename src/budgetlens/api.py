"""FastAPI MVP for CSV analysis and interactive budget simulation."""

from __future__ import annotations

import os
from collections import OrderedDict
from uuid import uuid4

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .bayesian import (
    hist_rate_state,
    posterior_overrun_probability,
    progress_state,
    season_state,
)
from .data import load_and_clean_csv, prepare_dataframe
from .forecast import forecast_department

SCHEMA_VERSION = "1.0"
MAX_ANALYSES = 50
RED_RISK_THRESHOLD = 0.67
YELLOW_RISK_THRESHOLD = 0.33

# Process-local cache for the four-week MVP. Restarting the API clears sessions.
_ANALYSES: OrderedDict[str, tuple[pd.DataFrame, str]] = OrderedDict()

app = FastAPI(
    title="BudgetLens AI API",
    version="0.1.0",
    description="CSV analysis and What-If budget simulation MVP",
)

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "BUDGETLENS_CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:5500,http://localhost:8501",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(HTTPException)
async def api_http_error_handler(_, exc: HTTPException) -> JSONResponse:
    code = {
        400: "INVALID_CSV",
        404: "ANALYSIS_NOT_FOUND",
        422: "INVALID_REQUEST",
    }.get(exc.status_code, "API_ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": str(exc.detail)}},
    )


@app.exception_handler(RequestValidationError)
async def api_validation_error_handler(_, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
        for item in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "Request validation failed",
                "details": details,
            }
        },
    )


class BudgetOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department: str = Field(min_length=1)
    annual_budget: float = Field(gt=0, allow_inf_nan=False)


class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=1)
    overrides: list[BudgetOverride] = Field(min_length=1)


def _risk_status(probability: float | None) -> str:
    if probability is None:
        return "yellow"
    if probability >= RED_RISK_THRESHOLD:
        return "red"
    if probability >= YELLOW_RISK_THRESHOLD:
        return "yellow"
    return "green"


def _department_analysis(
    full_data: pd.DataFrame,
    department: str,
    annual_budget_override: float | None,
) -> dict[str, object]:
    dept = full_data.loc[full_data["department"] == department].copy()
    years = dept["period"].str.slice(0, 4).astype(int)
    latest_year = int(years.max())
    current = dept.loc[years == latest_year].copy()
    previous = dept.loc[years == latest_year - 1].copy()

    monthly = current.groupby("period", sort=True, as_index=False).agg(
        budget=("budget", "sum"),
        actual=("actual", "sum"),
    )
    forecast = forecast_department(monthly, annual_budget_override)
    observed_budget = float(monthly["budget"].sum())
    observed_actual = float(monthly["actual"].sum())
    usage_rate = observed_actual / observed_budget if observed_budget > 0 else None
    variance = observed_actual - observed_budget

    evidence: dict[str, str] = {}
    if not previous.empty:
        previous_budget = float(previous["budget"].sum())
        previous_actual = float(previous["actual"].sum())
        if previous_budget > 0:
            evidence["歷史速率"] = hist_rate_state(previous_actual, previous_budget)

    latest_month = int(str(monthly.iloc[-1]["period"]).split("-")[1])
    month_numbers = [int(str(period).split("-")[1]) for period in monthly["period"]]
    complete_ytd = month_numbers == list(range(1, latest_month + 1))
    elapsed_months = latest_month if complete_ytd else len(monthly)
    annual_budget = float(forecast["annual_budget"])
    if annual_budget > 0:
        progress_ratio = observed_actual / annual_budget
        evidence["當前進度"] = progress_state(
            progress_ratio, min(max(elapsed_months, 1), 12)
        )

    mean_actual = float(monthly["actual"].mean())
    own_month_multiplier = (
        float(monthly.iloc[-1]["actual"]) / mean_actual if mean_actual > 0 else 1.0
    )
    evidence["季節因素"] = season_state(own_month_multiplier, industry_mult=1.0)
    bayesian_probability = posterior_overrun_probability(evidence)
    main_probability = forecast["overrun_probability"]
    status = _risk_status(
        float(main_probability) if main_probability is not None else bayesian_probability
    )

    monthly_rows: list[dict[str, object]] = []
    for row in monthly.to_dict(orient="records"):
        row_budget = float(row["budget"])
        row_actual = float(row["actual"])
        monthly_rows.append({
            "period": row["period"],
            "budget": row_budget,
            "actual": row_actual,
            "usage_rate": row_actual / row_budget if row_budget > 0 else None,
        })

    return {
        "department": department,
        "status": status,
        "budget": observed_budget,
        "actual": observed_actual,
        "variance": variance,
        "usage_rate": usage_rate,
        "variance_pct": variance / observed_budget if observed_budget > 0 else None,
        "period_count": int(len(monthly)),
        "latest_period": str(monthly.iloc[-1]["period"]),
        "annual_budget": annual_budget,
        "forecast": {
            "year_end_amount": forecast["year_end_amount"],
            "ci95": forecast["ci95"],
            "overrun_probability": forecast["overrun_probability"],
            "bayesian_overrun_probability": bayesian_probability,
            "method": forecast["method"],
            "periods_used": forecast["periods_used"],
        },
        "model_evidence": evidence,
        "diagnostics": [],
        "monthly": monthly_rows,
    }


def _build_response(
    frame: pd.DataFrame,
    analysis_id: str,
    currency: str,
    overrides: dict[str, float] | None = None,
) -> dict[str, object]:
    clean = prepare_dataframe(frame)
    overrides = overrides or {}
    unknown_departments = set(overrides) - set(clean["department"].unique().tolist())
    if unknown_departments:
        raise ValueError(f"Unknown department(s): {', '.join(sorted(unknown_departments))}")
    departments: list[dict[str, object]] = []
    for department in sorted(clean["department"].unique().tolist()):
        annual_budget = overrides.get(department)
        departments.append(_department_analysis(clean, department, annual_budget))
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_id": analysis_id,
        "currency": currency,
        "departments": departments,
    }


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/analyze")
def analyze_csv(
    file: UploadFile = File(...),
    currency: str = Form(default="TWD", min_length=1, max_length=8),
) -> dict[str, object]:
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a .csv file")
    try:
        frame = load_and_clean_csv(file.file)
        analysis_id = str(uuid4())
        response = _build_response(frame, analysis_id, currency)
    except (ValueError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        file.file.close()

    _ANALYSES[analysis_id] = (frame.copy(), currency)
    _ANALYSES.move_to_end(analysis_id)
    while len(_ANALYSES) > MAX_ANALYSES:
        _ANALYSES.popitem(last=False)
    return response


@app.post("/api/v1/simulate")
def simulate_budget(request: SimulateRequest) -> dict[str, object]:
    saved = _ANALYSES.get(request.analysis_id)
    if saved is None:
        raise HTTPException(
            status_code=404,
            detail="Analysis not found or expired; upload the CSV again.",
        )

    frame, currency = saved
    overrides = {item.department: item.annual_budget for item in request.overrides}
    if len(overrides) != len(request.overrides):
        raise HTTPException(status_code=422, detail="Each department can be overridden once")
    try:
        response = _build_response(frame, request.analysis_id, currency, overrides)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _ANALYSES.move_to_end(request.analysis_id)
    return response
