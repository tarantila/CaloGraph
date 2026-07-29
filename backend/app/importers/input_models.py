from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictStr,
)


def _reject_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL-Zeichen sind nicht erlaubt")
    return value


Text64 = Annotated[StrictStr, Field(max_length=64), AfterValidator(_reject_nul)]
Text128 = Annotated[StrictStr, Field(max_length=128), AfterValidator(_reject_nul)]
Text190 = Annotated[StrictStr, Field(max_length=190), AfterValidator(_reject_nul)]
Text255 = Annotated[StrictStr, Field(max_length=255), AfterValidator(_reject_nul)]


class ImportInputModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class HealthAutoPointInput(ImportInputModel):
    qty: Any = None
    value: Any = None
    startDate: Any = None
    start_date: Any = None
    date: Any = None
    endDate: Any = None
    end_date: Any = None
    source: Text190 | None = None
    sourceName: Text190 | None = None
    sourceBundle: Text255 | None = None
    sourceId: Text255 | None = None
    id: Text255 | None = None
    uuid: Text255 | None = None
    external_id: Text255 | None = None


class HealthAutoMetricInput(ImportInputModel):
    name: Text128 = ""
    units: Text64 | None = None
    unit: Text64 | None = None
    data: list[HealthAutoPointInput]


class HealthAutoPayloadInput(ImportInputModel):
    metrics: list[HealthAutoMetricInput]
    source: Text255 | None = None


class HealthAutoEnvelopeInput(ImportInputModel):
    data: HealthAutoPayloadInput


class CalographPointInput(ImportInputModel):
    type: Text128 = ""
    value: Any = None
    unit: Text64 | None = None
    start_at: Any = None
    end_at: Any = None
    timezone: Text64 | None = None
    source_name: Text190 | None = None
    source_identifier: Text255 | None = None
    id: Text255 | None = None
    uuid: Text255 | None = None
    external_id: Text255 | None = None


class CalographPayloadInput(ImportInputModel):
    samples: list[CalographPointInput]


class YazioUnitsInput(ImportInputModel):
    unit_energy: Text64 = "kcal"


class YazioMealInput(ImportInputModel):
    nutrients: dict[Text128, Any] | None = None


class YazioSummaryInput(ImportInputModel):
    units: YazioUnitsInput | None = None
    meals: dict[Text128, YazioMealInput] | None = None
    error: Any = None


class YazioDayInput(YazioSummaryInput):
    daily_summary: YazioSummaryInput | None = None
    energy: Any = None
    calories: Any = None
    protein: Any = None
    carb: Any = None
    carbs: Any = None
    fat: Any = None


class YazioExportRootInput(RootModel[dict[Text128, Any]]):
    pass
