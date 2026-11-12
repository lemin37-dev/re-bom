def prepare_new_image_slots(raw_notices, existing_rows):
    existing = {
        (row["desertion_no"], row["image_slot"]): row
        for row in existing_rows
    }

    new_rows = {}
    conflicts = []

    from rebom.storage.silver.transform_image import transform_images

    for raw in raw_notices:
        for image in transform_images(raw):
            key = (image["desertion_no"], image["image_slot"])

            if key in existing:
                if existing[key]["source_url"] != image["source_url"]:
                    conflicts.append(key)
                continue

            if key in new_rows:
                if new_rows[key]["source_url"] != image["source_url"]:
                    conflicts.append(key)
                continue

            new_rows[key] = {
                **image,
                "original_s3_key": None,
                "serving_s3_key": None,
                "thumb_s3_key": None,
                "sha256": None,
            }

    return list(new_rows.values()), conflicts


def prepare_safe_image_inserts(raw_notices, existing_rows):
    new_rows, conflicts = prepare_new_image_slots(
        raw_notices, existing_rows
    )

    if conflicts:
        raise ValueError(
            f"IMAGE_URL_CONFLICT: {sorted(set(conflicts))}"
        )

    existing_keys = {
        (row["desertion_no"], row["image_slot"])
        for row in existing_rows
    }

    new_keys = [
        (row["desertion_no"], row["image_slot"])
        for row in new_rows
    ]

    if len(new_keys) != len(set(new_keys)):
        raise RuntimeError("DUPLICATE_NEW_IMAGE_KEYS")

    if existing_keys.intersection(new_keys):
        raise RuntimeError("EXISTING_IMAGE_KEY_CONFLICT")

    return new_rows



def apply_image_incremental(catalog, table_name, expected_location, raw_notices):
    import pyarrow as pa

    table = catalog.load_table(table_name)

    if table.location().rstrip("/") != expected_location.rstrip("/"):
        raise RuntimeError("IMAGE_TABLE_LOCATION_MISMATCH")

    existing_rows = table.scan().to_arrow().to_pylist()
    existing_keys = [
        (row["desertion_no"], row["image_slot"])
        for row in existing_rows
    ]

    if len(existing_keys) != len(set(existing_keys)):
        raise RuntimeError("EXISTING_IMAGE_DUPLICATE_KEYS")

    new_rows = prepare_safe_image_inserts(raw_notices, existing_rows)

    if not new_rows:
        return {
            "inserted": 0,
            "total": len(existing_rows),
            "status": "NO_CHANGES",
        }

    arrow = pa.Table.from_pylist(
        new_rows,
        schema=table.schema().as_arrow(),
    )

    table.append(arrow)

    after = catalog.load_table(table_name).scan().to_arrow().to_pylist()
    after_keys = [
        (row["desertion_no"], row["image_slot"])
        for row in after
    ]

    if len(after_keys) != len(set(after_keys)):
        raise RuntimeError("IMAGE_DUPLICATE_AFTER_INSERT")

    if len(after) != len(existing_rows) + len(new_rows):
        raise RuntimeError("IMAGE_COUNT_MISMATCH")

    after_index = {
        (row["desertion_no"], row["image_slot"]): row
        for row in after
    }

    for row in new_rows:
        key = (row["desertion_no"], row["image_slot"])
        if after_index.get(key) != row:
            raise RuntimeError(f"IMAGE_INSERT_VERIFY_FAILED: {key}")

    return {
        "inserted": len(new_rows),
        "total": len(after),
        "status": "APPLIED",
    }
