import os
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog import load_catalog

base = Path(os.environ["LOCALAPPDATA"]) / "rebom-iceberg"

catalog = load_catalog(
    "rebom_local",
    type="sql",
    uri=f"sqlite:///{(base / 'catalog.db').as_posix()}",
    # Windows 로컬 경로는 file URI가 아닌 실제 파일시스템 경로로 전달한다.
# PyArrow가 /C:/... 경로를 해석하면서 발생하는 WinError 123을 방지한다.
warehouse=str(base / "warehouse"),
)

S = pa.string()
I = pa.int32()
F = pa.float64()
D = pa.date32()

# 기존 Python 정제 함수가 반환하는 컬럼을 기준으로 정의한다.
# desertion_no와 care_reg_no는 숫자가 아닌 식별자이므로 문자열로 보존한다.
notice_fields = [
    ("desertion_no", S),
    ("notice_no", S),
    ("care_reg_no", S),
    ("special_mark", S),
    ("happen_place", S),
    ("org_name", S),
    ("sex_code", S),
    ("neuter_code", S),
    ("end_reason_text", S),
    ("source_updated_at_raw", S),
    ("age_raw", S),
    ("birth_year", I),
    ("age_note", S),
    ("weight_raw", S),
    ("weight_min_kg", F),
    ("weight_max_kg", F),
    ("weight_parse_status", S),
    ("process_state_raw", S),
    ("status_group", S),
    ("quality_warning", S),
    ("closure_reason_code", S),
    ("happen_date", D),
    ("notice_start_date", D),
    ("notice_end_date", D),
    ("color_raw", S),
    ("color_normalized", S),
    ("color_parse_status", S),
    ("species_code", S),
    ("species_name", S),
    ("breed_code", S),
    ("breed_name_raw", S),
    ("breed_full_name_raw", S),
]

image_fields = [
    ("desertion_no", S),
    ("image_slot", I),
    ("source_url", S),
]

shelter_fields = [
    ("care_reg_no", S),
    ("care_name", S),
    ("care_address", S),
    ("care_tel", S),
    ("org_name", S),
]

history_fields = [
    ("desertion_no", S),
    ("observed_on", S),
    ("event_type", S),
    ("previous_state_raw", S),
    ("process_state_raw", S),
    ("status_group", S),
    ("closure_reason_code", S),
    ("source_updated_at_raw", S),
]

schemas = {
    "silver_animal_notice": notice_fields,
    "silver_animal_image": image_fields,
    "silver_shelter": shelter_fields,
    "silver_animal_status_history": history_fields,
}

created = 0

for name, fields in schemas.items():
    identifier = f"rebom_silver.{name}"

    if catalog.table_exists(identifier):
        print(f"EXISTS: {identifier}")
        continue

    arrow_schema = pa.schema([
        pa.field(
            column,
            dtype,
            nullable=(column not in ("desertion_no", "care_reg_no")),
        )
        for column, dtype in fields
    ])

    catalog.create_table(identifier, schema=arrow_schema)
    created += 1
    print(f"CREATED: {identifier} ({len(fields)} columns)")

print("CREATED_COUNT:", created)
print("TABLES:", sorted(
    ".".join(identifier)
    for identifier in catalog.list_tables("rebom_silver")
))

expected = set(schemas)
actual = {
    identifier[-1]
    for identifier in catalog.list_tables("rebom_silver")
}

if not expected.issubset(actual):
    raise RuntimeError("Some Silver tables are missing")

print("RESULT: PASS")

