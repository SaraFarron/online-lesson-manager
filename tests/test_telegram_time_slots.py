from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram.api import ResolvedLocalInstant
from telegram.shared.time_slots import (
    DEFAULT_SLOT_STEP,
    SLOT_PAGE_SIZE,
    clear_slots,
    find_slot,
    reset_slot_step,
    slot_id,
    slot_label,
    slot_local_start,
    slot_picker_data,
    store_slots,
    toggle_slot_step,
)


def _instant(utc: datetime, timezone: str) -> ResolvedLocalInstant:
    local = utc.astimezone(ZoneInfo(timezone))
    offset = local.strftime("%z")
    return ResolvedLocalInstant(
        local=local, utc=utc, utc_offset=f"{offset[:3]}:{offset[3:]}"
    )


def _day(count: int, timezone: str = "UTC") -> list[ResolvedLocalInstant]:
    start = datetime(2030, 1, 7, 0, tzinfo=UTC)
    return [_instant(start + timedelta(minutes=15 * n), timezone) for n in range(count)]


def test_labels_are_zero_padded_and_mark_only_ambiguous_fold_times() -> None:
    zone = "America/New_York"
    first = _instant(datetime(2030, 11, 3, 5, 30, tzinfo=UTC), zone)
    second = _instant(datetime(2030, 11, 3, 6, 30, tzinfo=UTC), zone)
    plain = _instant(datetime(2030, 11, 3, 14, 5, tzinfo=UTC), zone)
    assert slot_label(first, zone) == "01:30 (1st)"
    assert slot_label(second, zone) == "01:30 (2nd)"
    assert slot_label(plain, zone) == "09:05"
    assert slot_id(first) != slot_id(second)


def test_store_find_and_clear_slots_round_trip_exact_instants() -> None:
    data: dict = {}
    instants = _day(4, "Asia/Bangkok")
    store_slots(data, instants, "Asia/Bangkok")
    option = find_slot(data, slot_id(instants[2]))
    assert option is not None
    assert option["label"] == "07:30"
    assert option["utc"] == instants[2].utc.isoformat()
    assert slot_local_start(option) == datetime(2030, 1, 7, 7, 30)
    assert find_slot(data, "missing") is None
    clear_slots(data)
    assert data["slot_options"] == [] and data["slot_skip"] == 0
    assert find_slot(data, slot_id(instants[2])) is None


def test_picker_data_pages_only_when_needed() -> None:
    data: dict = {}
    store_slots(data, _day(SLOT_PAGE_SIZE), "UTC")
    view = slot_picker_data(data)
    assert len(view["slot_buttons"]) == SLOT_PAGE_SIZE
    assert not view["has_previous_slots"] and not view["has_next_slots"]

    store_slots(data, _day(SLOT_PAGE_SIZE + 5), "UTC")
    first = slot_picker_data(data)
    assert first["has_next_slots"] and not first["has_previous_slots"]
    assert first["slot_buttons"][0][0] == "00:00"
    data["slot_skip"] = SLOT_PAGE_SIZE
    last = slot_picker_data(data)
    assert len(last["slot_buttons"]) == 5
    assert last["has_previous_slots"] and not last["has_next_slots"]
    assert last["slot_buttons"][0][0] == "06:00"

    clear_slots(data)
    empty = slot_picker_data(data)
    assert empty["slot_buttons"] == [] and not empty["has_slots"]


def test_step_defaults_to_fifteen_and_toggles_between_fifteen_and_five() -> None:
    data: dict = {"slot_step": 5}
    reset_slot_step(data)
    assert data["slot_step"] == DEFAULT_SLOT_STEP == 15
    assert slot_picker_data(data)["slot_toggle_label"] == "Show 5-minute times"
    assert toggle_slot_step(data) == 5
    assert slot_picker_data(data)["slot_toggle_label"] == "Show 15-minute times"
    assert toggle_slot_step(data) == 15
