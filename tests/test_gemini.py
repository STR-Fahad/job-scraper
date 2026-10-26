from unittest.mock import MagicMock

import pytest
from google.genai import errors

from job_scraper import gemini
from job_scraper.cli import main


def test_missing_key_never_calls_sdk(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = MagicMock()
    monkeypatch.setattr(gemini.genai, "Client", client)
    assert gemini.check_connection() == {"status": "missing_key", "model": gemini.DEFAULT_MODEL}
    client.assert_not_called()


def test_synthetic_connection_and_model_override(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
    client = MagicMock()
    client.return_value.__enter__.return_value.models.generate_content.return_value.text = (
        "CONNECTED"
    )
    monkeypatch.setattr(gemini.genai, "Client", client)
    assert main(["gemini-check"]) == 0
    assert (
        client.return_value.__enter__.return_value.models.generate_content.call_args.kwargs["model"]
        == "test-model"
    )
    assert "synthetic-test-key" not in capsys.readouterr().out


def test_api_failure_does_not_expose_key(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    client = MagicMock()
    client.return_value.__enter__.return_value.models.generate_content.side_effect = RuntimeError(
        "synthetic-test-key"
    )
    monkeypatch.setattr(gemini.genai, "Client", client)
    assert main(["gemini-check"]) == 1
    output = capsys.readouterr()
    assert "synthetic-test-key" not in output.out + output.err
    assert "RuntimeError" in output.out


@pytest.mark.parametrize(
    ("code", "category"),
    [
        (401, "authentication"),
        (402, "billing"),
        (403, "permission_or_billing"),
        (404, "model_access"),
        (429, "quota"),
        (500, "temporary_server"),
        (504, "temporary_server"),
    ],
)
def test_safe_http_error_categories(code, category):
    exc = errors.APIError(code, {"error": {"message": "sensitive payload"}})
    result = gemini._safe_error(exc)
    assert result == {"http_status": str(code), "error_category": category}
    assert "sensitive payload" not in str(result)


def test_structured_billing_reason():
    exc = errors.APIError(
        403,
        {"error": {"details": [{"reason": "BILLING_DISABLED", "message": "sensitive"}]}},
    )
    assert gemini._safe_error(exc)["error_category"] == "billing"


def test_cli_reports_http_status_without_error_payload(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    client = MagicMock()
    client.return_value.__enter__.return_value.models.generate_content.side_effect = (
        errors.APIError(504, {"error": {"message": "synthetic-test-key sensitive payload"}})
    )
    monkeypatch.setattr(gemini.genai, "Client", client)
    assert main(["gemini-check", "--model", "gemini-3.8-flash"]) == 1
    output = capsys.readouterr()
    assert '"http_status": "504"' in output.out
    assert '"error_category": "temporary_server"' in output.out
    assert "synthetic-test-key" not in output.out + output.err
    assert "sensitive payload" not in output.out + output.err
