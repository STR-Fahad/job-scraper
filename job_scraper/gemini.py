"""Small, credential-safe Gemini connectivity probe using synthetic input only."""

import os

from google import genai
from google.genai import errors, types

DEFAULT_MODEL = "gemini-3.5-flash-lite"


def _safe_error(exc: errors.APIError) -> dict[str, str]:
    """Classify HTTP failures without emitting API response text."""
    code = exc.code
    category = {
        400: "invalid_request",
        401: "authentication",
        402: "billing",
        403: "permission_or_billing",
        404: "model_access",
        429: "quota",
        500: "temporary_server",
        502: "temporary_server",
        503: "temporary_server",
        504: "temporary_server",
    }.get(code, "api_error")
    if code == 403 and isinstance(exc.details, dict):
        details = exc.details.get("error", exc.details)
        if isinstance(details, dict):
            entries = details.get("details", [])
            if isinstance(entries, list) and any(
                isinstance(entry, dict)
                and entry.get("reason") in {"BILLING_DISABLED", "BILLING_NOT_ENABLED"}
                for entry in entries
            ):
                category = "billing"
    return {"http_status": str(code), "error_category": category}


def check_connection(model: str | None = None) -> dict[str, str]:
    """Make one bounded API call, without reading jobs or exposing credentials."""
    key = os.environ.get("GEMINI_API_KEY")
    selected_model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    if not key:
        return {"status": "missing_key", "model": selected_model}

    try:
        with genai.Client(api_key=key, http_options=types.HttpOptions(timeout=120_000)) as client:
            response = client.models.generate_content(
                model=selected_model,
                contents="Connectivity test with synthetic data. Reply with the word CONNECTED.",
                config=types.GenerateContentConfig(
                    temperature=0,
                    max_output_tokens=32,
                ),
            )
        if response.text and response.text.strip():
            return {"status": "connected", "model": selected_model}
        return {"status": "empty_response", "model": selected_model}
    except errors.APIError as exc:
        return {"status": "connection_failed", "model": selected_model, **_safe_error(exc)}
    except Exception as exc:
        # SDK exceptions can contain request details. Never emit their message or traceback.
        return {
            "status": "connection_failed",
            "model": selected_model,
            "error_type": type(exc).__name__,
        }
