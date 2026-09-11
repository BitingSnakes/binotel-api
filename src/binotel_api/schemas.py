"""Pydantic representations of Binotel response and webhook payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import phonenumbers
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


def parse_phone_number(value: Any) -> str | None:
    """Normalize supported phone input to E.164, returning None when invalid."""
    if value is None or str(value).strip() == "":
        return None

    number = str(value).strip()
    region: str | None = None
    if number.startswith("00"):
        number = "+" + number[2:]
    elif number.startswith("0") and len(number) == 10:
        region = "UA"
    elif not number.startswith("+"):
        return None

    try:
        parsed = phonenumbers.parse(number, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def parse_timestamp(value: Any) -> datetime | None:
    """Parse Unix or ISO 8601 input into a standard-library datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isnumeric()):
        return datetime.fromtimestamp(int(value), tz=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class BinotelData(BaseModel):
    """Provide common aliasing and compatibility behavior for Binotel models."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="ignore",
    )


class EmployeeData(BinotelData):
    """Represent an employee embedded in another Binotel response."""

    id: int | None = None
    name: str | None = None
    internal_number: str | None = None
    email: str | None = None


class LabelData(BinotelData):
    """Represent a customer label."""

    id: int
    name: str


class CustomerData(BinotelData):
    """Represent a Binotel customer and its associated metadata."""

    id: int
    name: str
    description: str | None = None
    email: str | None = None
    assigned_to_employee: EmployeeData | None = None
    numbers: list[str] = Field(default_factory=list)
    labels: list[LabelData] | None = None


class PbxNumberData(BinotelData):
    """Represent a public branch exchange number."""

    name: str | None = None
    number: str | None = None


class CallTrackingData(BinotelData):
    """Represent marketing attribution associated with a tracked call."""

    id: int
    ga_client_id: str
    ga_tracking_id: str
    utm_source: str = Field(alias="utm_source")
    utm_medium: str = Field(alias="utm_medium")
    utm_campaign: str = Field(alias="utm_campaign")
    utm_content: str = Field(alias="utm_content")
    utm_term: str = Field(alias="utm_term")
    ip_address: str
    geoip_country: str
    geoip_region: str
    geoip_city: str
    geoip_org: str
    domain: str
    time_spent_on_site_before_make_call: int
    first_visit_at: bool


class GetCallData(BinotelData):
    """Represent tracking data for a callback request."""

    id: int
    ga_client_id: str
    ga_tracking_id: str
    utm_source: str = Field(alias="utm_source")
    utm_medium: str = Field(alias="utm_medium")
    utm_campaign: str = Field(alias="utm_campaign")
    utm_content: str = Field(alias="utm_content")
    utm_term: str = Field(alias="utm_term")
    ip_address: str
    geoip_country: str
    geoip_region: str
    geoip_city: str
    geoip_org: str
    domain: str
    is_new_number: bool
    is_processed: bool
    requests_counter: int
    attempts_counter: int
    employees_dont_answer_counter: int
    full_url: str
    description: str
    created_at: datetime | None = None
    call_at: datetime | None = None
    processed_at: datetime | None = None

    @field_validator("created_at", "call_at", "processed_at", mode="before")
    @classmethod
    def _parse_dates(cls, value: Any) -> datetime | None:
        return parse_timestamp(value)


class HistoryData(BinotelData):
    """Represent one event in a call's history."""

    waitsec: int
    billsec: int
    disposition: str
    internal_number: str | None = None
    internal_additional_data: str | None = None
    employee_data: EmployeeData | None = None


class StatData(BinotelData):
    """Represent detailed statistics for a Binotel call."""

    company_id: int = Field(alias="companyID")
    general_call_id: int = Field(alias="generalCallID")
    call_id: int = Field(alias="callID")
    start_time: datetime
    call_type: int
    waitsec: int
    billsec: int
    disposition: str
    is_new_call: bool
    history_data: list[HistoryData] = Field(default_factory=list)
    pbx_number_data: PbxNumberData
    recording_status: str | None = None
    customer_data: CustomerData | None = None
    employee_data: EmployeeData | None = None
    call_tracking_data: CallTrackingData | None = None
    get_call_data: GetCallData | None = None
    sms_content: str | None = None
    internal_number: int | None = None
    internal_additional_data: str | None = None
    external_number: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_empty_values(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        result = dict(value)
        for key in (
            "customerData",
            "employeeData",
            "callTrackingData",
            "getCallData",
            "customer_data",
            "employee_data",
            "call_tracking_data",
            "get_call_data",
        ):
            if result.get(key) == "":
                result[key] = None
        history_key = "historyData" if "historyData" in result else "history_data"
        if result.get(history_key) == "":
            result[history_key] = []
        internal_key = "internalNumber" if "internalNumber" in result else "internal_number"
        internal = result.get(internal_key)
        try:
            result[internal_key] = int(internal) if internal not in (None, "") else None
        except (TypeError, ValueError):
            result[internal_key] = None
        return result

    @field_validator("start_time", mode="before")
    @classmethod
    def _parse_start_time(cls, value: Any) -> datetime:
        parsed = parse_timestamp(value)
        if parsed is None:
            raise ValueError("startTime is required")
        return parsed

    @field_validator("external_number", mode="before")
    @classmethod
    def _parse_external_number(cls, value: Any) -> str | None:
        return parse_phone_number(value)


class SettingsEndpointData(BinotelData):
    """Represent an employee telephony endpoint."""

    id: int
    login: str
    password: str
    internal_number: str
    status: list[Any] | dict[str, Any]


class SettingsEmployeeData(BinotelData):
    """Represent an employee returned by the settings API."""

    employee_id: int = Field(alias="employeeID")
    email: str
    name: str
    presence_state: str
    presence_state_updated_at: str
    role: str
    department: str
    wire_is_enabled: bool
    crm_is_enabled: bool
    chat_is_enabled: bool
    call_center_is_enabled: bool
    language: str
    endpoint_data: SettingsEndpointData | list[SettingsEndpointData]
    mobile_number: str | None = None

    @field_validator("endpoint_data", mode="before")
    @classmethod
    def _normalize_endpoint_map(cls, value: Any) -> Any:
        if isinstance(value, dict) and "id" not in value:
            return list(value.values())
        return value


class SettingsRouteData(BinotelData):
    """Represent an inbound call route."""

    id: int
    name: str
    description: str


class SettingsVoiceFileData(BinotelData):
    """Represent a voice file available to call scenarios."""

    id: int
    name: str
    type: str


class AnsweredTheCallData(BinotelData):
    """Represent an answered-call webhook payload."""

    general_call_id: int = Field(alias="generalCallID")
    call_type: int
    company_id: int = Field(alias="companyID")
    request_type: str
    pbx_number: str | None = None
    external_number: str | None = None
    internal_number: int | None = None

    @field_validator("external_number", mode="before")
    @classmethod
    def _parse_phone(cls, value: Any) -> str | None:
        return parse_phone_number(value)


class ApiCallSettingsData(BinotelData):
    """Represent an API call-settings webhook payload."""

    request_type: str
    external_number: str
    company_id: int = Field(alias="companyID")
    call_type: int
    pbx_number: str | None = None
    internal_number: int | None = None

    @field_validator("external_number", mode="before")
    @classmethod
    def _parse_phone(cls, value: Any) -> str | None:
        return parse_phone_number(value)


class ReceivedTheCallData(AnsweredTheCallData):
    """Represent a received-call webhook payload."""

    method: str
    did_number: str | None = None
    did: str | None = None
    src_number: str | None = None


class HangupTheCallData(BinotelData):
    """Represent a call-hangup webhook payload."""

    general_call_id: int = Field(alias="generalCallID")
    billsec: int
    disposition: str
    request_type: str
    method: str


class TransferredTheCallData(BinotelData):
    """Represent a transferred-call webhook payload."""

    internal_number: int
    general_call_id: int = Field(alias="generalCallID")
    company_id: int = Field(alias="companyID")
    request_type: str


class ApiCallCompletedData(BinotelData):
    """Represent a completed API-call webhook payload."""

    request_type: str
    call_details: list[Any] | dict[str, Any]
