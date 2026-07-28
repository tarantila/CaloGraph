import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast

import requests
from yazio_exporter.auth import CLIENT_ID, CLIENT_SECRET  # type: ignore[import-untyped]
from yazio_exporter.client import YazioClient  # type: ignore[import-untyped]
from yazio_exporter.exceptions import AuthenticationError  # type: ignore[import-untyped]
from yazio_exporter.export_days import (  # type: ignore[import-untyped]
    fetch_days_concurrent,
)
from yazio_exporter.export_nutrients import (  # type: ignore[import-untyped]
    fetch_multiple,
)
from yazio_exporter.utils import serialize_day_data  # type: ignore[import-untyped]

from app.config import settings
from app.micronutrients import YAZIO_MICRONUTRIENT_IDS

MAX_WORKER_INPUT_BYTES = 16 * 1024
MAX_WORKER_OUTPUT_BYTES = 32 * 1024 * 1024


class YazioTransportError(RuntimeError):
    pass


class YazioTransportAuthenticationError(YazioTransportError):
    pass


class YazioTransportDeadlineError(YazioTransportError):
    pass


class _WorkerAuthenticationError(RuntimeError):
    pass


class _WorkerProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _TransportOptions:
    connect_timeout: float
    read_timeout: float
    request_workers: int


class _BoundedYazioClient(YazioClient):  # type: ignore[misc]
    def __init__(self, options: _TransportOptions) -> None:
        super().__init__()
        self._calograph_options = options

    def get(
        self,
        endpoint: str,
        max_retries: int = 1,
        **kwargs: Any,
    ) -> requests.Response:
        del max_retries
        kwargs["timeout"] = (
            self._calograph_options.connect_timeout,
            self._calograph_options.read_timeout,
        )
        kwargs["allow_redirects"] = False
        return cast(
            requests.Response,
            super().get(endpoint, max_retries=1, **kwargs),
        )


def validate_yazio_credentials_transport(email: str, password: str) -> None:
    _run_worker(
        {
            "operation": "validate",
            "email": email,
            "password": password,
            **_worker_options(),
        },
        settings.yazio_login_deadline_seconds,
    )


def fetch_yazio_payload_transport(
    email: str,
    password: str,
    start_day: date,
    end_day: date,
    include_micronutrients: bool,
) -> dict[str, Any]:
    result = _run_worker(
        {
            "operation": "fetch",
            "email": email,
            "password": password,
            "start_day": start_day.isoformat(),
            "end_day": end_day.isoformat(),
            "include_micronutrients": include_micronutrients,
            **_worker_options(),
        },
        settings.yazio_operation_deadline_seconds,
    )
    if not isinstance(result, dict):
        raise YazioTransportError("YAZIO worker returned an invalid payload")
    return result


def _worker_options() -> dict[str, float | int]:
    return {
        "connect_timeout": settings.yazio_connect_timeout_seconds,
        "read_timeout": settings.yazio_read_timeout_seconds,
        "request_workers": settings.yazio_request_workers,
    }


def _run_worker(payload: dict[str, object], deadline_seconds: int) -> object:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_WORKER_INPUT_BYTES:
        raise YazioTransportError("YAZIO worker input is too large")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "app.services.yazio_transport", "--worker"],
            input=encoded,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=deadline_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise YazioTransportDeadlineError(
            "YAZIO operation exceeded its absolute deadline"
        ) from exc
    except OSError as exc:
        raise YazioTransportError("YAZIO worker could not be started") from exc

    if len(completed.stdout) > MAX_WORKER_OUTPUT_BYTES:
        raise YazioTransportError("YAZIO worker output is too large")
    try:
        response = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YazioTransportError("YAZIO worker returned an invalid response") from exc
    if not isinstance(response, dict):
        raise YazioTransportError("YAZIO worker returned an invalid response")
    if response.get("ok") is True:
        return response.get("result")

    kind = response.get("kind")
    if kind == "authentication":
        raise YazioTransportAuthenticationError("YAZIO authentication failed")
    if kind == "timeout":
        raise YazioTransportDeadlineError("YAZIO request timed out")
    raise YazioTransportError("YAZIO provider request failed")


def _login(client: _BoundedYazioClient, email: str, password: str) -> str:
    url = f"{client.base_url}/{client.api_version}/oauth/token"
    response = client.session.post(
        url,
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "username": email,
            "password": password,
            "grant_type": "password",
        },
        timeout=(
            client._calograph_options.connect_timeout,
            client._calograph_options.read_timeout,
        ),
        allow_redirects=False,
    )
    if response.status_code in {400, 401, 403}:
        raise _WorkerAuthenticationError
    if 300 <= response.status_code < 400:
        raise _WorkerProviderError
    response.raise_for_status()
    data = response.json()
    token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise _WorkerProviderError
    return token


def _execute_worker(payload: dict[str, object]) -> object:
    operation = payload.get("operation")
    email = payload.get("email")
    password = payload.get("password")
    if (
        operation not in {"validate", "fetch"}
        or not isinstance(email, str)
        or not 3 <= len(email) <= 320
        or not isinstance(password, str)
        or not 1 <= len(password) <= 1_024
    ):
        raise ValueError("Invalid worker input")
    connect_timeout = payload.get("connect_timeout")
    read_timeout = payload.get("read_timeout")
    request_workers = payload.get("request_workers")
    if (
        not isinstance(connect_timeout, (int, float))
        or isinstance(connect_timeout, bool)
        or not isinstance(read_timeout, (int, float))
        or isinstance(read_timeout, bool)
        or not isinstance(request_workers, int)
        or isinstance(request_workers, bool)
    ):
        raise ValueError("Invalid worker options")
    options = _TransportOptions(
        connect_timeout=float(connect_timeout),
        read_timeout=float(read_timeout),
        request_workers=request_workers,
    )
    if not (
        0.1 <= options.connect_timeout <= 30
        and 1 <= options.read_timeout <= 120
        and 1 <= options.request_workers <= 10
    ):
        raise ValueError("Invalid worker options")

    client = _BoundedYazioClient(options)
    try:
        token = _login(client, email, password)
        if operation == "validate":
            return None
        client.set_token(token)
        start_day = date.fromisoformat(str(payload["start_day"]))
        end_day = date.fromisoformat(str(payload["end_day"]))
        if start_day > end_day or (end_day - start_day).days >= 366:
            raise ValueError("Invalid YAZIO date range")
        dates = [
            (start_day + timedelta(days=offset)).isoformat()
            for offset in range((end_day - start_day).days + 1)
        ]
        raw_days = fetch_days_concurrent(
            client,
            dates,
            ["daily_summary"],
            max_workers=options.request_workers,
        )
        for day_data in raw_days.values():
            if isinstance(day_data.get("daily_summary"), Exception):
                raise _WorkerProviderError
        micronutrients = (
            fetch_multiple(
                client,
                list(YAZIO_MICRONUTRIENT_IDS),
                start_day.isoformat(),
                end_day.isoformat(),
                max_workers=options.request_workers,
            )
            if payload.get("include_micronutrients") is True
            else None
        )
        return {
            "days": {
                day: serialize_day_data(day_data)
                for day, day_data in raw_days.items()
            },
            **(
                {"nutrients": micronutrients}
                if micronutrients is not None
                else {}
            ),
        }
    finally:
        client.session.close()


def _worker_main() -> int:
    try:
        encoded = sys.stdin.buffer.read(MAX_WORKER_INPUT_BYTES + 1)
        if len(encoded) > MAX_WORKER_INPUT_BYTES:
            raise ValueError("Worker input is too large")
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            raise ValueError("Worker input must be an object")
        result = _execute_worker(payload)
        response: dict[str, object] = {"ok": True, "result": result}
    except (_WorkerAuthenticationError, AuthenticationError):
        response = {"ok": False, "kind": "authentication"}
    except requests.Timeout:
        response = {"ok": False, "kind": "timeout"}
    except Exception:
        response = {"ok": False, "kind": "provider"}
    sys.stdout.write(json.dumps(response, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__" and "--worker" in sys.argv:
    raise SystemExit(_worker_main())
