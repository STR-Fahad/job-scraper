"""Provider facts mapped conservatively; eligibility is separate from arrangement."""

from .models import Compensation, Salary


def workplace(source, raw):
    if source in {"himalayas", "remotive"}:
        return "remote"
    if source in {"ashby", "lever"}:
        value = str(raw.get("workplaceType") or "").lower().replace("-", "")
        return {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}.get(value, "unknown")
    if source == "arbeitnow" and raw.get("remote") is True:
        return "remote"
    # A false remote flag does not distinguish hybrid from onsite.
    # Greenhouse's free-text location is not a structured workplace declaration.
    return "unknown"


def period(value):
    return {
        "1 year": "annual",
        "year": "annual",
        "yearly": "annual",
        "annual": "annual",
        "1 month": "monthly",
        "month": "monthly",
        "monthly": "monthly",
        "1 week": "weekly",
        "week": "weekly",
        "weekly": "weekly",
        "1 hour": "hourly",
        "hour": "hourly",
        "hourly": "hourly",
        "fortnightly": "fortnightly",
    }.get(str(value).lower())


def ashby_salary(raw):
    if not raw:
        return None
    components = raw.get("summaryComponents")
    if not components:
        components = [
            c for tier in raw.get("compensationTiers", []) for c in tier.get("components", [])
        ]
    parsed = [
        Compensation(
            minimum=c.get("minValue"),
            maximum=c.get("maxValue"),
            currency=c.get("currencyCode"),
            period=period(c.get("interval")),
            compensation_type={
                "Salary": "base_salary",
                "Bonus": "bonus",
                "EquityPercentage": "equity",
                "Equity": "equity",
            }.get(c.get("compensationType"), "unknown"),
        )
        for c in components
    ]
    bases = [c for c in parsed if c.compensation_type == "base_salary"]
    values = bases[0].model_dump() if len(bases) == 1 else {}
    return Salary(
        **values,
        text=raw.get("scrapeableCompensationSalarySummary"),
        details=raw,
        components=parsed,
    )


def greenhouse_salary(ranges):
    if not ranges:
        return None
    parsed = [
        Compensation(
            minimum=r["min_cents"] / 100 if r.get("min_cents") is not None else None,
            maximum=r["max_cents"] / 100 if r.get("max_cents") is not None else None,
            currency=r.get("currency_type"),
            period=None,
            compensation_type="unknown"
            if "ote" in str(r.get("title", "")).lower()
            else "base_salary",
        )
        for r in ranges
    ]
    # No period is supplied by this API. Never infer annual from amount or title.
    return Salary(
        **(parsed[0].model_dump() if len(parsed) == 1 else {}),
        details={"pay_input_ranges": ranges},
        components=parsed,
    )
