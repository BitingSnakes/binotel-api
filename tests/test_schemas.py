from datetime import UTC

from binotel_api.schemas import SettingsEmployeeData, StatData, parse_phone_number


def stat_payload() -> dict:
    return {
        "companyID": "1",
        "generalCallID": "2",
        "callID": "3",
        "startTime": "1725840000",
        "callType": "0",
        "waitsec": "4",
        "billsec": "5",
        "disposition": "ANSWER",
        "isNewCall": 1,
        "historyData": "",
        "pbxNumberData": {"name": "Main", "number": "0441234567"},
        "customerData": "",
        "employeeData": "",
        "callTrackingData": "",
        "getCallData": "",
        "internalNumber": "not-a-number",
        "externalNumber": "0671234567",
    }


def test_stat_normalizes_api_edge_cases() -> None:
    data = StatData.model_validate(stat_payload())

    assert data.history_data == []
    assert data.customer_data is None
    assert data.internal_number is None
    assert data.external_number == "+380671234567"
    assert data.start_time.tzinfo == UTC


def test_phone_number_supported_formats() -> None:
    assert parse_phone_number("+380671234567") == "+380671234567"
    assert parse_phone_number("00380671234567") == "+380671234567"
    assert parse_phone_number("garbage") is None


def test_stat_accepts_python_field_names_without_losing_internal_number() -> None:
    payload = stat_payload()
    payload.pop("internalNumber")
    payload["internal_number"] = "801"

    assert StatData.model_validate(payload).internal_number == 801


def test_employee_endpoint_map_is_normalized() -> None:
    employee = SettingsEmployeeData.model_validate(
        {
            "employeeID": 1,
            "email": "employee@example.test",
            "name": "Employee",
            "presenceState": "online",
            "presenceStateUpdatedAt": "2026-09-11 00:00:00",
            "role": "employee",
            "department": "Sales",
            "wireIsEnabled": True,
            "crmIsEnabled": True,
            "chatIsEnabled": True,
            "callCenterIsEnabled": True,
            "language": "uk",
            "endpointData": {
                "801": {
                    "id": 1,
                    "login": "employee",
                    "password": "secret",
                    "internalNumber": "801",
                    "status": [],
                }
            },
        }
    )

    assert isinstance(employee.endpoint_data, list)
    assert employee.endpoint_data[0].internal_number == "801"
