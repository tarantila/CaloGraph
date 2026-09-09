import json
import subprocess
import sys
import threading
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
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
from app.services.yazio_provider import (
    YazioConsumedProduct,
    YazioConsumedSimpleProduct,
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
    YazioProductProfile,
    YazioProviderAuthenticationError,
    YazioProviderDeadlineError,
    YazioProviderError,
    YazioProviderInvalidResponseError,
    YazioProviderNetworkTimeoutError,
    YazioProviderRateLimitedError,
    YazioProviderUnavailableError,
    YazioServing,
    get_yazio_food_diary_provider,
    get_yazio_provider,
)

MAX_WORKER_INPUT_BYTES = 16 * 1024
MAX_WORKER_OUTPUT_BYTES = 32 * 1024 * 1024


class YazioTransportError(RuntimeError):
    pass


class YazioTransportAuthenticationError(YazioTransportError):
    pass


class YazioTransportVersionBlockedError(YazioTransportError):
    pass


class YazioTransportRateLimitedError(YazioTransportError):
    def __init__(self, retry_after: int | None = None) -> None:
        self.retry_after = retry_after
        super().__init__("YAZIO provider rate limit exceeded")


class YazioTransportUnavailableError(YazioTransportError):
    pass


class YazioTransportNetworkTimeoutError(YazioTransportError):
    pass


class YazioTransportInvalidResponseError(YazioTransportError):
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


def validate_yazio_credentials_transport(
    email: str,
    password: str,
    *,
    provider_mode: str | None = None,
) -> None:
    _run_worker(
        {
            "operation": "validate",
            "email": email,
            "password": password,
            "provider_mode": provider_mode or settings.yazio_provider,
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
    *,
    provider_mode: str | None = None,
) -> dict[str, Any]:
    result = _run_worker(
        {
            "operation": "fetch",
            "email": email,
            "password": password,
            "start_day": start_day.isoformat(),
            "end_day": end_day.isoformat(),
            "include_micronutrients": include_micronutrients,
            "provider_mode": provider_mode or settings.yazio_provider,
            **_worker_options(),
        },
        settings.yazio_operation_deadline_seconds,
    )
    if not isinstance(result, dict):
        raise YazioTransportInvalidResponseError("YAZIO worker returned an invalid payload")
    return result


def _raise_domain_provider_error(error: YazioTransportError) -> None:
    if isinstance(error, YazioTransportAuthenticationError):
        raise YazioProviderAuthenticationError from error
    if isinstance(error, YazioTransportVersionBlockedError):
        from app.services.yazio_provider import YazioProviderVersionBlockedError

        raise YazioProviderVersionBlockedError from error
    if isinstance(error, YazioTransportRateLimitedError):
        raise YazioProviderRateLimitedError(error.retry_after) from error
    if isinstance(error, YazioTransportNetworkTimeoutError):
        raise YazioProviderNetworkTimeoutError from error
    if isinstance(error, YazioTransportDeadlineError):
        raise YazioProviderDeadlineError from error
    if isinstance(error, YazioTransportInvalidResponseError):
        raise YazioProviderInvalidResponseError from error
    raise YazioProviderUnavailableError from error


def fetch_yazio_domain_transport(
    email: str,
    password: str,
    start_day: date,
    end_day: date,
) -> tuple[dict[str, Any], YazioFoodDiary]:
    try:
        result = _run_worker(
            {
                "operation": "fetch_domain",
                "email": email,
                "password": password,
                "start_day": start_day.isoformat(),
                "end_day": end_day.isoformat(),
                "include_micronutrients": True,
                "provider_mode": "sdk",
                **_worker_options(),
            },
            settings.yazio_operation_deadline_seconds,
        )
        if not isinstance(result, dict):
            raise YazioTransportInvalidResponseError("YAZIO worker returned an invalid payload")
        aggregate = result.get("aggregate")
        diary = result.get("diary")
        if not isinstance(aggregate, dict) or not isinstance(diary, dict):
            raise YazioTransportInvalidResponseError(
                "YAZIO worker returned an invalid domain payload"
            )
        try:
            normalized_diary = _decode_food_diary(diary)
        except YazioTransportInvalidResponseError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise YazioTransportInvalidResponseError from exc
        return aggregate, normalized_diary
    except YazioTransportError as exc:
        _raise_domain_provider_error(exc)


def _encode_transport_value(value: object) -> object:
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, date):
        return {"__date__": value.isoformat()}
    if is_dataclass(value):
        return {
            item.name: _encode_transport_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _encode_transport_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode_transport_value(item) for item in value]
    return value


def _decode_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise YazioTransportInvalidResponseError("YAZIO worker returned an invalid domain field")
    return value


def _decode_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    data = _decode_mapping(value)
    raw = data.get("__decimal__")
    if not isinstance(raw, str):
        raise YazioTransportInvalidResponseError("YAZIO worker returned an invalid decimal")
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise YazioTransportInvalidResponseError from exc


def _decode_date(value: object) -> date:
    data = _decode_mapping(value)
    raw = data.get("__date__")
    if not isinstance(raw, str):
        raise YazioTransportInvalidResponseError("YAZIO worker returned an invalid date")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise YazioTransportInvalidResponseError from exc


def _decode_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    data = _decode_mapping(value)
    raw = data.get("__datetime__")
    if not isinstance(raw, str):
        raise YazioTransportInvalidResponseError("YAZIO worker returned an invalid datetime")
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise YazioTransportInvalidResponseError from exc


def _decode_nutrients(value: object) -> YazioNutrientValues:
    data = _decode_mapping(value)
    additional = _decode_mapping(data.get("additional"))
    return YazioNutrientValues(
        energy=_decode_decimal(data.get("energy")),
        protein=_decode_decimal(data.get("protein")),
        carb=_decode_decimal(data.get("carb")),
        fat=_decode_decimal(data.get("fat")),
        fiber=_decode_decimal(data.get("fiber")),
        sugar=_decode_decimal(data.get("sugar")),
        saturated_fat=_decode_decimal(data.get("saturated_fat")),
        salt=_decode_decimal(data.get("salt")),
        additional={
            key: decimal
            for key, item in additional.items()
            if (decimal := _decode_decimal(item)) is not None
        },
    )


def _decode_serving(value: object) -> YazioServing:
    data = _decode_mapping(value)
    metadata = _decode_mapping(data.get("metadata"))
    return YazioServing(
        label=data.get("label") if isinstance(data.get("label"), str) else None,
        amount=_decode_decimal(data.get("amount")),
        unit=data.get("unit") if isinstance(data.get("unit"), str) else None,
        metadata=metadata,
    )


def _decode_food_diary(data: dict[str, object]) -> YazioFoodDiary:
    consumed_products = []
    for raw in data.get("consumed_products", []):
        item = _decode_mapping(raw)
        consumed_products.append(
            YazioConsumedProduct(
                consumed_item_id=str(item["consumed_item_id"]),
                product_id=str(item["product_id"]),
                amount=_decode_decimal(item.get("amount")),
                provider_civil_datetime=_decode_datetime(item.get("provider_civil_datetime")),
                local_date=_decode_date(item["local_date"]),
                daytime=item.get("daytime") if isinstance(item.get("daytime"), str) else None,
                serving=item.get("serving") if isinstance(item.get("serving"), str) else None,
                serving_quantity=_decode_decimal(item.get("serving_quantity")),
                provider_timezone=(
                    item.get("provider_timezone")
                    if isinstance(item.get("provider_timezone"), str)
                    else None
                ),
                metadata=_decode_mapping(item.get("metadata")),
            )
        )
    consumed_simple_products = []
    for raw in data.get("consumed_simple_products", []):
        item = _decode_mapping(raw)
        consumed_simple_products.append(
            YazioConsumedSimpleProduct(
                consumed_item_id=str(item["consumed_item_id"]),
                amount=_decode_decimal(item.get("amount")),
                provider_civil_datetime=_decode_datetime(item.get("provider_civil_datetime")),
                local_date=_decode_date(item["local_date"]),
                daytime=item.get("daytime") if isinstance(item.get("daytime"), str) else None,
                nutrients=_decode_nutrients(item["nutrients"]),
                serving=item.get("serving") if isinstance(item.get("serving"), str) else None,
                serving_quantity=_decode_decimal(item.get("serving_quantity")),
                provider_timezone=(
                    item.get("provider_timezone")
                    if isinstance(item.get("provider_timezone"), str)
                    else None
                ),
                name=item.get("name") if isinstance(item.get("name"), str) else None,
                metadata=_decode_mapping(item.get("metadata")),
            )
        )
    profiles = []
    for raw in data.get("product_profiles", []):
        item = _decode_mapping(raw)
        servings = tuple(_decode_serving(serving) for serving in item.get("servings", []))
        profiles.append(
            YazioProductProfile(
                product_id=str(item["product_id"]),
                name=item.get("name") if isinstance(item.get("name"), str) else None,
                producer=item.get("producer") if isinstance(item.get("producer"), str) else None,
                category=item.get("category") if isinstance(item.get("category"), str) else None,
                base_unit=item.get("base_unit") if isinstance(item.get("base_unit"), str) else None,
                nutrients=_decode_nutrients(item["nutrients"]),
                servings=servings,
                eans=tuple(str(ean) for ean in item.get("eans", [])),
                language=item.get("language") if isinstance(item.get("language"), str) else None,
                countries=tuple(str(country) for country in item.get("countries", [])),
                updated_at=_decode_datetime(item.get("updated_at")),
                is_verified=item.get("is_verified") if isinstance(item.get("is_verified"), bool) else None,
                is_private=item.get("is_private") if isinstance(item.get("is_private"), bool) else None,
                is_deleted=item.get("is_deleted") if isinstance(item.get("is_deleted"), bool) else None,
                metadata=_decode_mapping(item.get("metadata")),
            )
        )
    summaries = []
    for raw in data.get("daily_summaries", []):
        item = _decode_mapping(raw)
        summaries.append(
            YazioDailyNutrientSummary(
                local_date=_decode_date(item["local_date"]),
                nutrients=_decode_nutrients(item["nutrients"]),
                energy_goal=_decode_decimal(item.get("energy_goal")),
                metadata=_decode_mapping(item.get("metadata")),
            )
        )
    return YazioFoodDiary(
        requested_start_day=_decode_date(data["requested_start_day"]),
        requested_end_day=_decode_date(data["requested_end_day"]),
        consumed_products=tuple(consumed_products),
        consumed_simple_products=tuple(consumed_simple_products),
        product_profiles=tuple(profiles),
        daily_summaries=tuple(summaries),
    )


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
        process = subprocess.Popen(
            [sys.executable, "-m", "app.services.yazio_transport", "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise YazioTransportError("YAZIO worker could not be started") from exc

    output = bytearray()
    output_error: list[OSError] = []
    output_too_large = threading.Event()

    def read_output() -> None:
        if process.stdout is None:
            return
        try:
            while chunk := process.stdout.read(64 * 1024):
                remaining = MAX_WORKER_OUTPUT_BYTES + 1 - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(chunk) > remaining or len(output) > MAX_WORKER_OUTPUT_BYTES:
                    output_too_large.set()
                    with suppress(OSError):
                        process.kill()
                    return
        except OSError as exc:
            output_error.append(exc)
            with suppress(OSError):
                process.kill()

    reader = threading.Thread(
        target=read_output,
        name="calograph-yazio-worker-output",
        daemon=True,
    )
    reader.start()
    try:
        if process.stdin is None:
            raise YazioTransportError("YAZIO worker input pipe is unavailable")
        process.stdin.write(encoded)
        process.stdin.close()
        process.wait(timeout=deadline_seconds)
    except subprocess.TimeoutExpired as exc:
        with suppress(OSError):
            process.kill()
        process.wait()
        reader.join()
        raise YazioTransportDeadlineError(
            "YAZIO operation exceeded its absolute deadline"
        ) from exc
    except OSError as exc:
        with suppress(OSError):
            process.kill()
        process.wait()
        reader.join()
        raise YazioTransportError("YAZIO worker communication failed") from exc
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()

    reader.join()
    if output_error:
        raise YazioTransportError("YAZIO worker output could not be read") from output_error[0]
    if output_too_large.is_set() or len(output) > MAX_WORKER_OUTPUT_BYTES:
        raise YazioTransportError("YAZIO worker output is too large")
    try:
        response = json.loads(output)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YazioTransportError("YAZIO worker returned an invalid response") from exc
    if not isinstance(response, dict):
        raise YazioTransportError("YAZIO worker returned an invalid response")
    if response.get("ok") is True:
        return response.get("result")

    kind = response.get("kind")
    if kind == "authentication":
        raise YazioTransportAuthenticationError("YAZIO authentication failed")
    if kind == "version_blocked":
        raise YazioTransportVersionBlockedError(
            "YAZIO API client version is blocked"
        )
    if kind == "rate_limited":
        retry_after = response.get("retry_after")
        if (
            isinstance(retry_after, bool)
            or not isinstance(retry_after, int)
            or not 0 <= retry_after <= 3_600
        ):
            retry_after = None
        raise YazioTransportRateLimitedError(retry_after)
    if kind == "unavailable":
        raise YazioTransportUnavailableError(
            "YAZIO provider is temporarily unavailable"
        )
    if kind in {"network_timeout", "timeout"}:
        raise YazioTransportNetworkTimeoutError("YAZIO provider request timed out")
    if kind == "deadline":
        raise YazioTransportDeadlineError(
            "YAZIO operation exceeded its absolute deadline"
        )
    if kind == "invalid_response":
        raise YazioTransportInvalidResponseError(
            "YAZIO provider returned an invalid response"
        )
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
    provider_mode = payload.get("provider_mode")
    if (
        operation not in {"validate", "fetch", "fetch_domain"}
        or provider_mode not in {"legacy", "sdk"}
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

    if provider_mode == "sdk":
        provider = get_yazio_provider("sdk")
        if operation == "validate":
            provider.validate_credentials(email, password)
            return None
        try:
            start_day = date.fromisoformat(str(payload["start_day"]))
            end_day = date.fromisoformat(str(payload["end_day"]))
        except (KeyError, TypeError, ValueError) as exc:
            from app.services.yazio_provider import YazioProviderInvalidResponseError

            raise YazioProviderInvalidResponseError from exc
        aggregate = provider.fetch(
            email,
            password,
            start_day,
            end_day,
            payload.get("include_micronutrients") is True,
        ).payload
        if operation == "fetch_domain":
            diary = get_yazio_food_diary_provider().fetch_food_diary(
                email,
                password,
                start_day,
                end_day,
            )
            return {
                "aggregate": aggregate,
                "diary": _encode_transport_value(diary),
            }
        return aggregate

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
    except YazioProviderError as exc:
        response = {
            "ok": False,
            "kind": exc.kind,
            **(
                {"retry_after": exc.retry_after}
                if exc.retry_after is not None
                else {}
            ),
        }
    except requests.Timeout:
        response = {"ok": False, "kind": "network_timeout"}
    except Exception:
        response = {"ok": False, "kind": "unavailable"}
    sys.stdout.write(json.dumps(response, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__" and "--worker" in sys.argv:
    raise SystemExit(_worker_main())
