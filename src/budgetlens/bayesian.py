"""Bayesian-network risk inference based on the Day 3 model assumptions.

The root-node priors and risk CPT are expert-set starting assumptions, not
parameters learned from company data. Replace/calibrate them when labelled
historical observations become available.
"""

from __future__ import annotations

from itertools import product
from math import isfinite
from typing import Mapping

from pgmpy.factors.discrete import TabularCPD
from pgmpy.inference import VariableElimination
from pgmpy.models import DiscreteBayesianNetwork

STATES = {
    "歷史速率": ["快", "持平", "慢"],
    "當前進度": ["提前", "落後"],
    "季節因素": ["旺季", "淡季"],
    "超支風險": ["超支", "不超支"],
}

EDGES = [
    ("歷史速率", "超支風險"),
    ("當前進度", "超支風險"),
    ("季節因素", "超支風險"),
]

# Initial expert priors. Each list follows STATES order for its variable.
DEFAULT_PRIORS = {
    "歷史速率": [0.25, 0.50, 0.25],
    "當前進度": [0.50, 0.50],
    "季節因素": [0.30, 0.70],
}

BASE = 5
ADD = {
    "歷史速率": {"快": 30, "持平": 12, "慢": 0},
    "當前進度": {"提前": 45, "落後": 0},
    "季節因素": {"旺季": 15, "淡季": 0},
}

CAUSE_COPY = {
    ("歷史速率", "快"): (
        "去年實際支出超過預算 5% 以上",
        "回顧去年超支項目，並確認今年是否需要調高相關預算。",
    ),
    ("歷史速率", "持平"): (
        "去年實際支出接近預算",
        "持續追蹤支出趨勢，並檢查近期是否出現新的成本變化。",
    ),
    ("歷史速率", "慢"): (
        "去年實際支出低於預算 5% 以上",
        "檢查今年與去年支出結構是否不同，避免直接沿用去年較低的支出水準。",
    ),
    ("當前進度", "提前"): (
        "累計支出進度快於年度時間進度",
        "檢查剩餘預算與未來承諾支出，必要時調整支出節奏。",
    ),
    ("當前進度", "落後"): (
        "累計支出進度未快於年度時間進度",
        "確認後續已核准但尚未入帳的支出，避免低估年底支出。",
    ),
    ("季節因素", "旺季"): (
        "季節模型判定目前屬於旺季",
        "將旺季支出納入後續月份預測與預算配置。",
    ),
    ("季節因素", "淡季"): (
        "季節模型判定目前屬於淡季",
        "檢查是否有非季節性的額外支出推高預算風險。",
    ),
}


def _validate_probability_vector(variable: str, values: list[float]) -> None:
    if len(values) != len(STATES[variable]):
        raise ValueError(f"{variable} prior needs {len(STATES[variable])} values")
    if any(not isfinite(value) or value < 0 for value in values):
        raise ValueError(f"{variable} prior values must be finite and non-negative")
    if abs(sum(values) - 1.0) > 1e-9:
        raise ValueError(f"{variable} prior probabilities must sum to 1")


def _resolve_priors(
    priors: Mapping[str, list[float]] | None,
) -> dict[str, list[float]]:
    result = {name: values.copy() for name, values in DEFAULT_PRIORS.items()}
    if priors:
        unknown = set(priors) - set(DEFAULT_PRIORS)
        if unknown:
            raise ValueError(f"Unknown prior variable(s): {', '.join(sorted(unknown))}")
        for name, values in priors.items():
            result[name] = [float(value) for value in values]
    for name, values in result.items():
        _validate_probability_vector(name, values)
    return result


def hist_rate_state(last_year_actual: float, last_year_budget: float) -> str:
    """Classify last year's actual/budget ratio using Day 3 cutoffs."""
    budget = float(last_year_budget)
    actual = float(last_year_actual)
    if not isfinite(budget) or budget <= 0:
        raise ValueError("last_year_budget must be a finite number greater than zero")
    if not isfinite(actual) or actual < 0:
        raise ValueError("last_year_actual must be a finite non-negative number")
    ratio = actual / budget
    if ratio > 1.05:
        return "快"
    if ratio < 0.95:
        return "慢"
    return "持平"


def progress_state(used_ratio: float, month: int, threshold: float = 0.0) -> str:
    """Compare cumulative budget usage with the elapsed-year fraction."""
    ratio = float(used_ratio)
    cut = float(threshold)
    if not isfinite(ratio) or ratio < 0:
        raise ValueError("used_ratio must be a finite non-negative number")
    if not isinstance(month, int) or isinstance(month, bool) or not 1 <= month <= 12:
        raise ValueError("month must be an integer from 1 to 12")
    if not isfinite(cut):
        raise ValueError("threshold must be finite")
    gap = ratio - month / 12
    return "提前" if gap > cut else "落後"


def season_state(
    own_mult: float,
    industry_mult: float,
    w_own: float = 0.4,
    w_ind: float = 0.6,
    cut: float = 1.5,
) -> str:
    """Classify a month as peak/off-peak using the Day 3 weighted rule."""
    own, industry, own_weight, industry_weight, threshold = map(
        float, (own_mult, industry_mult, w_own, w_ind, cut)
    )
    if any(not isfinite(value) for value in (own, industry, own_weight, industry_weight, threshold)):
        raise ValueError("season inputs must be finite numbers")
    if own < 0 or industry < 0 or own_weight < 0 or industry_weight < 0:
        raise ValueError("season multipliers and weights cannot be negative")
    if abs(own_weight + industry_weight - 1.0) > 1e-9:
        raise ValueError("w_own and w_ind must sum to 1")
    return "旺季" if own_weight * own + industry_weight * industry > threshold else "淡季"


def p_overrun(historical_rate: str, current_progress: str, seasonality: str) -> float:
    """Return the Day 3 CPT probability for a complete evidence assignment."""
    evidence = {
        "歷史速率": historical_rate,
        "當前進度": current_progress,
        "季節因素": seasonality,
    }
    for variable, state in evidence.items():
        if state not in STATES[variable]:
            raise ValueError(f"Invalid state for {variable}: {state!r}")
    score = BASE + sum(ADD[variable][state] for variable, state in evidence.items())
    return min(max(score / 100.0, 0.0), 1.0)


def build_model(
    priors: Mapping[str, list[float]] | None = None,
) -> DiscreteBayesianNetwork:
    """Build and validate the Day 3 Bayesian network and its CPDs."""
    root_priors = _resolve_priors(priors)
    model = DiscreteBayesianNetwork(EDGES)

    cpds = [
        TabularCPD(
            variable=variable,
            variable_card=len(STATES[variable]),
            values=[[probability] for probability in root_priors[variable]],
            state_names={variable: STATES[variable]},
        )
        for variable in root_priors
    ]

    evidence_variables = ["歷史速率", "當前進度", "季節因素"]
    assignments = list(product(*(STATES[name] for name in evidence_variables)))
    overrun_probabilities = [
        p_overrun(historical_rate, current_progress, seasonality)
        for historical_rate, current_progress, seasonality in assignments
    ]
    risk_values = [overrun_probabilities, [1.0 - value for value in overrun_probabilities]]
    state_names = {name: STATES[name] for name in ["超支風險", *evidence_variables]}
    cpds.append(
        TabularCPD(
            variable="超支風險",
            variable_card=2,
            values=risk_values,
            evidence=evidence_variables,
            evidence_card=[len(STATES[name]) for name in evidence_variables],
            state_names=state_names,
        )
    )

    model.add_cpds(*cpds)
    if not model.check_model():
        raise RuntimeError("Bayesian network CPDs failed model validation")
    return model


def posterior_overrun_probability(
    evidence: Mapping[str, str] | None = None,
    priors: Mapping[str, list[float]] | None = None,
) -> float:
    """Compute P(超支 | evidence) with exact variable elimination.

    Evidence keys may be any subset of 歷史速率, 當前進度, 季節因素.
    With all three observed, the result equals the corresponding CPT value;
    with partial or no evidence, the network marginalizes missing causes using
    the configured (or explicitly provisional default) priors.
    """
    evidence = dict(evidence or {})
    unknown = set(evidence) - set(DEFAULT_PRIORS)
    if unknown:
        raise ValueError(f"Unknown evidence variable(s): {', '.join(sorted(unknown))}")
    for variable, state in evidence.items():
        if state not in STATES[variable]:
            raise ValueError(f"Invalid state for {variable}: {state!r}")

    model = build_model(priors)
    distribution = VariableElimination(model).query(
        variables=["超支風險"],
        evidence=evidence or None,
        show_progress=False,
    )
    state_index = distribution.state_names["超支風險"].index("超支")
    return float(distribution.values[state_index])


def infer_overrun_causes(
    priors: Mapping[str, list[float]] | None = None,
    top_k: int = 3,
) -> dict[str, object]:
    """Reverse-infer the most risk-elevating states after observing overrun.

    For each cause node, calculate P(cause state | overrun) and compare
    P(overrun | cause state) with the network's baseline P(overrun). The
    primary cause is the highest-probability state with the largest positive
    risk difference. This is a model-based explanation, not a causal finding
    validated against company data.
    """
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise ValueError("top_k must be a positive integer")

    root_priors = _resolve_priors(priors)
    model = build_model(root_priors)
    inference = VariableElimination(model)
    outcome = {"超支風險": "超支"}
    baseline_probability = posterior_overrun_probability(priors=root_priors)
    ranked_causes: list[dict[str, object]] = []

    for variable in DEFAULT_PRIORS:
        posterior = inference.query(
            variables=[variable], evidence=outcome, show_progress=False
        )
        posterior_states = posterior.state_names[variable]
        posterior_values = posterior.values.tolist()

        state_results: list[dict[str, object]] = []
        for state, posterior_probability in zip(posterior_states, posterior_values):
            state_index = STATES[variable].index(state)
            prior_probability = root_priors[variable][state_index]
            if prior_probability <= 0:
                continue

            risk_given_state = inference.query(
                variables=["超支風險"],
                evidence={variable: state},
                show_progress=False,
            )
            overrun_index = risk_given_state.state_names["超支風險"].index("超支")
            conditional_risk = float(risk_given_state.values[overrun_index])

            state_results.append({
                "cause": variable,
                "state": state,
                "reason": CAUSE_COPY[(variable, state)][0],
                "recommendation": CAUSE_COPY[(variable, state)][1],
                "prior_probability": float(prior_probability),
                "posterior_probability": float(posterior_probability),
                "posterior_probability_lift": float(posterior_probability - prior_probability),
                "overrun_probability_given_state": conditional_risk,
                "risk_probability_lift": conditional_risk - baseline_probability,
            })

        # First pick the state most strongly associated with increased risk
        # within this cause; then rank cause nodes by that risk increase.
        state_results.sort(
            key=lambda item: (
                float(item["risk_probability_lift"]),
                float(item["posterior_probability_lift"]),
                float(item["posterior_probability"]),
            ),
            reverse=True,
        )
        if state_results:
            ranked_causes.append(state_results[0])

    ranked_causes.sort(
        key=lambda item: (
            float(item["risk_probability_lift"]),
            float(item["posterior_probability_lift"]),
        ),
        reverse=True,
    )
    primary_cause = ranked_causes[0] if ranked_causes else None
    return {
        "observed_outcome": "超支",
        "baseline_overrun_probability": baseline_probability,
        "primary_cause": primary_cause,
        "ranked_causes": ranked_causes[:top_k],
    }


def risk_report(evidence: Mapping[str, str] | None = None) -> dict[str, float | str]:
    """Return the posterior and complementary non-overrun probability."""
    probability = posterior_overrun_probability(evidence)
    return {
        "risk_state": "超支" if probability >= 0.5 else "不超支",
        "overrun_probability": probability,
        "no_overrun_probability": 1.0 - probability,
    }
