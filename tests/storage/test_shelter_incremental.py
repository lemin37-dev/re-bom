from rebom.storage.silver.shelter_incremental import prepare_shelter_delta


def raw(key, name="Shelter", address="Seoul", tel="02-1234-5678", org="Seoul"):
    return {
        "careRegNo": key,
        "careNm": name,
        "careAddr": address,
        "careTel": tel,
        "orgNm": org,
    }


def silver(key):
    return {
        "care_reg_no": key,
        "care_name": "Shelter",
        "care_address": "Seoul",
        "care_tel": "02-1234-5678",
        "org_name": "Seoul",
    }


def test_insert():
    result = prepare_shelter_delta([raw("A")], [])
    assert result["insert_rows"] == [silver("A")]


def test_update():
    result = prepare_shelter_delta(
        [raw("A", address="Gyeonggi")],
        [silver("A")],
    )
    assert len(result["update_rows"]) == 1
    assert result["update_rows"][0]["care_address"] == "Gyeonggi"
    assert result["changes"][0]["previous_value"] == "Seoul"


def test_unchanged():
    result = prepare_shelter_delta([raw("A")], [silver("A")])
    assert result["delta_rows"] == []
    assert result["unchanged_keys"] == ["A"]


def test_null_preserves_previous():
    result = prepare_shelter_delta(
        [raw("A", tel=None)],
        [silver("A")],
    )
    assert result["delta_rows"] == []


def test_conflict_preserves_previous():
    result = prepare_shelter_delta(
        [raw("A", tel="111"), raw("A", tel="222")],
        [silver("A")],
    )
    assert result["delta_rows"] == []
    assert len(result["conflicts"]) == 1


def test_new_conflict_withheld():
    result = prepare_shelter_delta(
        [raw("A", tel="111"), raw("A", tel="222")],
        [],
    )
    assert result["insert_rows"][0]["care_tel"] is None


def test_not_observed_preserved():
    result = prepare_shelter_delta(
        [raw("A")],
        [silver("A"), silver("B")],
    )
    assert result["not_observed_keys"] == ["B"]
    assert result["delta_rows"] == []


def test_missing_key():
    result = prepare_shelter_delta([raw(None)], [])
    assert result["delta_rows"] == []
    assert len(result["missing_keys"]) == 1
