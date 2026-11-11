import pyarrow as pa

from rebom.storage.silver.aws_catalog import get_aws_catalog

PREFIX = "s3://rebom-data-559987919299/bench_tmp/b-storage-test/iceberg"

S = pa.string()
I = pa.int32()
F = pa.float64()
D = pa.date32()
B = pa.bool_()

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

history_fields = [
    ("desertion_no", S),
    ("observed_on", S),
    ("event_type", S),
    ("previous_state_raw", S),
    ("process_state_raw", S),
    ("status_group", S),
    ("closure_reason_code", S),
    ("source_updated_at_raw", S),
    ("run_id", S),
    ("fetched_at", S),
    ("confirmed_by", S),
    ("is_inferred", B),
]

tables = {
    "silver_animal_notice_b_test": notice_fields,
    "silver_animal_status_history_b_test": history_fields,
}

catalog = get_aws_catalog()

for name, fields in tables.items():
    identifier = f"rebom_silver.{name}"
    location = f"{PREFIX}/{name}"

    if catalog.table_exists(identifier):
        raise RuntimeError(f"Table already exists: {identifier}")

    schema = pa.schema([
        pa.field(column, data_type)
        for column, data_type in fields
    ])

    table = catalog.create_table(
        identifier,
        schema=schema,
        location=location,
    )

    print("CREATED:", table.name())
    print("LOCATION:", table.location())
    print("COLUMNS:", len(table.schema().fields))

print("AWS_SILVER_TABLE_CREATE: PASS")
