"""Tests for washer and dryer course option overrides."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.smartthinq_sensors.sensor import LGESensor
from custom_components.smartthinq_sensors.wideq.core_exceptions import (
    InvalidCourseOptions,
)
from custom_components.smartthinq_sensors.wideq.device_info import DeviceType
from custom_components.smartthinq_sensors.wideq.devices.washerDryer import (
    VT_CTRL_COURSE_INFO,
    CourseType,
    WMDevice,
)

COURSE_INFO = {
    "function": [
        {
            "value": "temp",
            "default": "TEMP_40",
            "selectable": ["TEMP_COLD", "TEMP_40", "TEMP_60"],
        },
        {
            "value": "spin",
            "default": "SPIN_800",
            "selectable": ["SPIN_800", "SPIN_1200"],
        },
        {
            "value": "steam",
            "default": "STEAM_OFF",
        },
    ]
}

SMART_COURSE_INFO = {
    "_comment": "Jeans",
    "Course": "DELICATE",
    "courseType": "SmartCourse",
    "downloadEnable": True,
    "controlEnable": True,
    "function": [
        {"value": "soilWash", "default": "SOILWASH_NORMAL"},
        {"value": "spin", "default": "SPIN_Max"},
        {"value": "temp", "default": "TEMP_20"},
    ],
}


def _make_device(course_info=COURSE_INFO, *, info_v2=True, option_keys=None):
    """Create a minimally initialized device backed by model course data."""
    device = object.__new__(WMDevice)
    device._sub_key = None
    device._course_infos = {"Cotton": "1"}
    device._smart_course_infos = {"Jeans": "JEANS"}
    device._course_keys = {
        CourseType.COURSE: "courseType",
        CourseType.SMARTCOURSE: "smartCourseType",
        CourseType.OPCOURSE: None,
    }
    device._course_overrides = {}
    device._selected_course = None
    device._download_course_id = None
    device._downloaded_course_id = "JEANS"
    device._initial_bit_start = False
    course_data = {
        "courseType": {"1": course_info},
        "smartCourseType": {"JEANS": SMART_COURSE_INFO},
    }
    device._model_info = SimpleNamespace(
        is_info_v2=info_v2,
        reference_values=lambda key: course_data.get(key),
        value=lambda key: None,
        option_keys=lambda sub_key: option_keys or [],
        config_value=lambda key: {
            "downloadedCourseType": "downloadedCourseType",
            "maxDownloadCourseNum": 1,
        }.get(key),
        value_exist=lambda key: key in course_data or key == "downloadedCourseType",
        get_control_cmd=lambda command: (
            {"command": "Set", "data": {"washerDryer": {}}}
            if command == "WMDownload"
            else None
        ),
    )
    status_data = {"downloadedCourseType": "JEANS"}
    device._status = SimpleNamespace(
        as_dict=status_data,
        update_status=lambda key, value: status_data.update({key: value}) or True,
    )
    return device


def test_validate_course_overrides_from_model_data():
    """Overrides are normalized to the exact values supplied by model data."""
    device = _make_device()

    assert device._validate_course_overrides(
        "Cotton", {"temp": "TEMP_60", "spin": "SPIN_1200"}
    ) == {"temp": "TEMP_60", "spin": "SPIN_1200"}


def test_course_options_exposes_model_defaults_and_selectable_values():
    """Course metadata is available to Home Assistant for local validation."""
    device = _make_device()

    assert device.course_options == {
        "Cotton": {
            "temp": {
                "default": "TEMP_40",
                "selectable": ["TEMP_COLD", "TEMP_40", "TEMP_60"],
            },
            "spin": {
                "default": "SPIN_800",
                "selectable": ["SPIN_800", "SPIN_1200"],
            },
        },
        "Jeans": {},
    }


async def test_course_option_selects_stage_and_clear_validated_values():
    """Option selects expose only course values and update staged overrides."""
    device = _make_device()
    device._selected_course = "Cotton"
    device._initial_bit_start = True

    with patch.object(
        WMDevice,
        "remote_start_enabled",
        new_callable=PropertyMock,
        return_value=True,
    ):
        assert device.course_option_list("temp") == [
            "Course default",
            "TEMP_COLD",
            "TEMP_40",
            "TEMP_60",
        ]
        assert device.course_option_enabled("temp") is True
        assert device.selected_course_option("temp") == "Course default"

        await device.select_course_option("temp", "TEMP_60")
        assert device.selected_course_option("temp") == "TEMP_60"
        assert device.prepared_course_options == {"temp": "TEMP_60"}

        await device.select_course_option("temp", "Course default")
        assert device.selected_course_option("temp") == "Course default"
        assert device.prepared_course_options == {}


async def test_course_option_select_rejects_value_not_supported_by_course():
    """Direct select calls cannot bypass model-derived validation."""
    device = _make_device()
    device._selected_course = "Cotton"
    device._initial_bit_start = True

    with (
        patch.object(
            WMDevice,
            "remote_start_enabled",
            new_callable=PropertyMock,
            return_value=True,
        ),
        pytest.raises(InvalidCourseOptions) as err,
    ):
        await device.select_course_option("temp", "TEMP_95")

    assert err.value.translation_key == "invalid_course_option_value"


@pytest.mark.parametrize(
    "dry_level", ["DRYLEVEL_NORMAL", "DRYLEVEL_60", "DRYLEVEL_LOW"]
)
def test_validate_dry_level_from_global_model_enum(dry_level):
    """ThinQ2 dry choices remain usable when a dry course omits selectable."""
    dry_course_info = {
        "function": [{"value": "dryLevel", "default": "DRYLEVEL_NORMAL"}]
    }
    device = _make_device(dry_course_info)
    device._model_info.value = lambda key: (
        SimpleNamespace(
            options={
                "NOT_SELECTED": "No selection",
                "NO_DRYLEVEL": "No dry level",
                "DRYLEVEL_NORMAL": "Normal",
                "DRYLEVEL_60": "60 minutes",
                "DRYLEVEL_LOW": "Low temperature",
                "DRYLEVEL_COOLING": "Cooling",
            }
        )
        if key == "dryLevel"
        else None
    )

    assert device._validate_course_overrides("Cotton", {"dryLevel": dry_level}) == {
        "dryLevel": dry_level
    }


@pytest.mark.parametrize(
    ("course_default", "dry_level"),
    [
        ("NOT_SELECTED", "DRYLEVEL_NORMAL"),
        ("DRYLEVEL_NORMAL", "DRYLEVEL_COOLING"),
    ],
)
def test_validate_dry_level_fallback_rejects_inactive_or_internal_values(
    course_default, dry_level
):
    """The global fallback excludes non-dry courses and internal states."""
    dry_course_info = {"function": [{"value": "dryLevel", "default": course_default}]}
    device = _make_device(dry_course_info)
    device._model_info.value = lambda key: SimpleNamespace(
        options={"DRYLEVEL_NORMAL": "Normal", "DRYLEVEL_COOLING": "Cooling"}
    )

    with pytest.raises(InvalidCourseOptions):
        device._validate_course_overrides("Cotton", {"dryLevel": dry_level})


@pytest.mark.parametrize(
    ("course", "overrides", "translation_key"),
    [
        (None, {"temp": "TEMP_60"}, "course_required_for_overrides"),
        ("Unknown", {"temp": "TEMP_60"}, "invalid_course"),
        ("Cotton", {"dryLevel": "DRY_NORMAL"}, "invalid_course_option"),
        ("Cotton", {"temp": "TEMP_95"}, "invalid_course_option_value"),
    ],
)
def test_validate_course_overrides_rejects_invalid_input(
    course, overrides, translation_key
):
    """Invalid course settings are rejected locally before an LG API call."""
    device = _make_device()

    with pytest.raises(InvalidCourseOptions) as err:
        device._validate_course_overrides(course, overrides)

    assert err.value.translation_key == translation_key


def test_prepare_course_info_applies_validated_overrides():
    """Validated values replace course defaults in a ThinQ2 start payload."""
    device = _make_device()
    device._course_overrides = {"temp": "TEMP_60", "spin": "SPIN_1200"}

    result = device._prepare_course_info(
        {},
        "1",
        COURSE_INFO,
        CourseType.COURSE,
        False,
        "courseType",
        None,
    )

    assert result["courseType"] == "1"
    assert result["temp"] == "TEMP_60"
    assert result["spin"] == "SPIN_1200"
    assert result["steam"] == "STEAM_OFF"
    assert result[VT_CTRL_COURSE_INFO] == COURSE_INFO


def test_prepare_course_info_applies_override_to_thinq1_bitfield():
    """A ThinQ1 option override updates the packed option value."""
    course_info = {
        "function": [{"value": "HotWash", "default": "0", "selectable": ["0", "1"]}]
    }
    device = _make_device(course_info, info_v2=False, option_keys=["Option1"])
    device._model_info.bit_index = lambda option, bit: (
        2 if (option, bit) == ("Option1", "HotWash") else None
    )
    device._course_overrides = {"HotWash": "1"}

    result = device._prepare_course_info(
        {"Option1": "0"},
        "1",
        course_info,
        CourseType.COURSE,
        False,
        "Course",
        None,
    )

    assert result["Option1"] == "4"
    assert "HotWash" not in result


async def test_remote_start_clears_overrides_after_failed_command():
    """Transient override state never leaks into a later remote start."""
    device = _make_device()
    device._initial_bit_start = True
    device._remote_start_pressed = False
    device._validate_course_overrides = lambda course, overrides: {"temp": "TEMP_60"}
    device.select_start_course = AsyncMock()
    device._get_cmd_keys = lambda command: ["ctrl", "start", "key"]
    device.set = AsyncMock(side_effect=RuntimeError("API failed"))

    with (
        patch.object(
            WMDevice,
            "remote_start_enabled",
            new_callable=PropertyMock,
            return_value=True,
        ),
        pytest.raises(RuntimeError, match="API failed"),
    ):
        await device.remote_start("Cotton", {"temp": "TEMP_60"})

    assert device._course_overrides == {}
    assert device._remote_start_pressed is False


async def test_prepare_course_stages_settings_without_sending_command():
    """A prepared preset is validated and retained without an LG API call."""
    device = _make_device()
    device._initial_bit_start = True
    device.set = AsyncMock()

    with patch.object(
        WMDevice,
        "remote_start_enabled",
        new_callable=PropertyMock,
        return_value=True,
    ):
        await device.prepare_course("Cotton", {"temp": "TEMP_60", "spin": "SPIN_1200"})

    assert device.prepared_course == "Cotton"
    assert device.prepared_course_options == {
        "temp": "TEMP_60",
        "spin": "SPIN_1200",
    }
    device.set.assert_not_awaited()


def test_prepared_course_survives_power_save_sleep():
    """A staged preset remains available while course selection is disabled."""
    device = _make_device()
    device._initial_bit_start = False
    device._selected_course = "Cotton"
    device._course_overrides = {"temp": "TEMP_60"}

    with patch.object(
        WMDevice,
        "remote_start_enabled",
        new_callable=PropertyMock,
        return_value=False,
    ):
        assert device.select_course_enabled is False

    assert device.prepared_course == "Cotton"
    assert device.prepared_course_options == {"temp": "TEMP_60"}


async def test_remote_start_uses_prepared_course_and_options():
    """A parameterless start consumes the previously prepared preset."""
    device = _make_device()
    device._initial_bit_start = True
    device._remote_start_pressed = False
    device._selected_course = "Cotton"
    device._course_overrides = {"temp": "TEMP_60", "spin": "SPIN_1200"}
    device._get_cmd_keys = lambda command: ["ctrl", "start", "key"]
    sent = {}

    async def capture_set(ctrl_key, command, *, key=None):
        sent.update(
            course=device.prepared_course,
            options=device.prepared_course_options,
        )

    device.set = AsyncMock(side_effect=capture_set)

    with patch.object(
        WMDevice,
        "remote_start_enabled",
        new_callable=PropertyMock,
        return_value=True,
    ):
        await device.remote_start()

    assert sent == {
        "course": "Cotton",
        "options": {"temp": "TEMP_60", "spin": "SPIN_1200"},
    }
    assert device.prepared_course is None
    assert device.prepared_course_options == {}
    assert device._remote_start_pressed is True


async def test_wake_up_works_when_power_save_status_is_empty():
    """Power-save sleep can be exited even when standby is not reported."""
    device = _make_device()
    device._stand_by = False
    device._status = None
    device._get_cmd_keys = lambda command: ["ctrl", "wake", None]
    device.set = AsyncMock()

    with patch.object(
        WMDevice,
        "_state_power_on_init",
        new_callable=PropertyMock,
        return_value="POWER_ON",
    ):
        await device.wake_up()

    device.set.assert_awaited_once_with("ctrl", "wake")
    assert device._stand_by is False


async def test_sensor_reports_override_error_as_service_validation_error():
    """The HA action returns a translated validation error to the caller."""
    api = SimpleNamespace(
        type=DeviceType.WASHER,
        device=SimpleNamespace(
            remote_start=AsyncMock(
                side_effect=InvalidCourseOptions(
                    "invalid_course_option_value",
                    option="temp",
                    course="Cotton",
                    value="TEMP_95",
                    available_values="TEMP_40, TEMP_60",
                )
            )
        ),
    )
    entity = SimpleNamespace(_api=api)

    with pytest.raises(ServiceValidationError) as err:
        await LGESensor.async_remote_start(entity, "Cotton", {"temp": "TEMP_95"})

    assert err.value.translation_domain == "smartthinq_sensors"
    assert err.value.translation_key == "invalid_course_option_value"


async def test_sensor_prepares_course_and_refreshes_entities():
    """The HA prepare action stages its data and refreshes entity state."""
    api = SimpleNamespace(
        type=DeviceType.WASHER,
        device=SimpleNamespace(prepare_course=AsyncMock()),
        async_set_updated=Mock(),
    )
    entity = SimpleNamespace(_api=api)

    await LGESensor.async_prepare_course(entity, "Cotton", {"temp": "TEMP_60"})

    api.device.prepare_course.assert_awaited_once_with("Cotton", {"temp": "TEMP_60"})
    api.async_set_updated.assert_called_once_with()


def test_prepare_download_course_payload_matches_model_template():
    """WMDownload uses the model's exact course keys and Smart Course defaults."""
    device = _make_device()
    device._course_keys = {
        CourseType.COURSE: "courseFL24inchBaseTitan",
        CourseType.SMARTCOURSE: "smartCourseFL24inchBaseTitan",
        CourseType.OPCOURSE: None,
    }
    device._model_info.reference_values = lambda key: {
        "courseFL24inchBaseTitan": {"1": COURSE_INFO},
        "smartCourseFL24inchBaseTitan": {"JEANS": SMART_COURSE_INFO},
    }.get(key)
    device._model_info.config_value = lambda key: {
        "downloadedCourseType": "downloadedCourseFL24inchBaseTitan",
        "maxDownloadCourseNum": 1,
    }.get(key)
    device._model_info.value_exist = lambda key: key in {
        "courseFL24inchBaseTitan",
        "smartCourseFL24inchBaseTitan",
        "downloadedCourseFL24inchBaseTitan",
    }
    device._download_course_id = "JEANS"
    command = {
        "command": "Set",
        "data": {
            "washerDryer": {
                "courseDownloadType": "COURSEDATA",
                "courseDownloadDataLength": 21,
                "course": "temp",
                "soilWash": "NO_SOILWASH",
                "spin": "NOT_SELECTED",
                "temp": "NO_TEMP",
                "rinse": "NO_RINSE",
                "dryLevel": "NOT_SELECTED",
                "reserveTimeHour": 0,
                "reserveTimeMinute": 0,
                "loadItemWasher": "LOADITEM_OFF",
                "turboWash": "TURBOWASH_OFF",
                "creaseCare": "CREASECARE_OFF",
                "steamSoftener": "STEAMSOFTENER_OFF",
                "ecoHybrid": "ECOHYBRID_OFF",
                "medicRinse": "MEDICRINSE_OFF",
                "rinseSpin": "RINSE_SPIN_OFF",
                "preWash": "PREWASH_OFF",
                "steam": "STEAM_OFF",
                "initialBit": "INITIAL_BIT_OFF",
                "remoteStart": "REMOTE_START_OFF",
                "wrinkleCare": "WRINKLECARE_OFF",
                "doorLock": "DOOR_LOCK_OFF",
                "childLock": "CHILDLOCK_OFF",
                "SmartCourse": "temp",
            }
        },
    }

    result = device._prepare_command_v2(command, "WMDownload")
    payload = result["dataSetList"]["washerDryer"]

    assert payload["courseDownloadType"] == "COURSEDATA"
    assert payload["courseDownloadDataLength"] == 21
    assert payload["courseFL24inchBaseTitan"] == "DELICATE"
    assert payload["smartCourseFL24inchBaseTitan"] == "JEANS"
    assert payload["downloadedCourseFL24inchBaseTitan"] == "JEANS"
    assert payload["soilWash"] == "SOILWASH_NORMAL"
    assert payload["spin"] == "SPIN_Max"
    assert payload["temp"] == "TEMP_20"
    assert "course" not in payload
    assert "SmartCourse" not in payload


async def test_download_course_sends_command_and_stages_course():
    """A successful download replaces the slot and prepares the new course."""
    device = _make_device()
    device.set = AsyncMock()

    await device.download_course("Jeans")

    device.set.assert_awaited_once_with("WMDownload", "WMDownload", key="WMDownload")
    assert device.downloaded_course == "Jeans"
    assert device.prepared_course == "Jeans"
    assert device.prepared_course_options == {}
    assert device._download_course_id is None


async def test_sensor_downloads_course_and_refreshes_entities():
    """The HA action delegates the download and refreshes entity attributes."""
    api = SimpleNamespace(
        type=DeviceType.WASHER,
        device=SimpleNamespace(download_course=AsyncMock()),
        async_set_updated=Mock(),
    )
    entity = SimpleNamespace(_api=api)

    await LGESensor.async_download_course(entity, "Jeans")

    api.device.download_course.assert_awaited_once_with("Jeans")
    api.async_set_updated.assert_called_once_with()


def test_sensor_exposes_prepared_course_attributes():
    """The main washer sensor exposes the staged preset for verification."""
    entity = SimpleNamespace(
        _is_default=True,
        _wrap_device=SimpleNamespace(extra_state_attributes={"existing": "value"}),
        _api=SimpleNamespace(
            type=DeviceType.WASHER,
            device=SimpleNamespace(
                course_options={
                    "Cotton": {
                        "temp": {
                            "default": "TEMP_40",
                            "selectable": ["TEMP_40", "TEMP_60"],
                        }
                    }
                },
                downloadable_course_list=["Jeans", "Sportswear"],
                downloaded_course="Jeans",
                download_course_limit=1,
                prepared_course="Cotton",
                prepared_course_options={"temp": "TEMP_60"},
            ),
        ),
    )

    attributes = LGESensor.extra_state_attributes.fget(entity)

    assert attributes == {
        "existing": "value",
        "course_options": {
            "Cotton": {
                "temp": {
                    "default": "TEMP_40",
                    "selectable": ["TEMP_40", "TEMP_60"],
                }
            }
        },
        "downloadable_courses": ["Jeans", "Sportswear"],
        "downloaded_course": "Jeans",
        "download_course_limit": 1,
        "prepared_course": "Cotton",
        "prepared_course_options": {"temp": "TEMP_60"},
    }
