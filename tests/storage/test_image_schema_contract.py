import ast
from pathlib import Path

from rebom.storage.silver.photo_pipeline import PHOTO_FIELDS


def test_image_schema_supports_photo_manifest():
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts/storage/rebom_create_silver_tables.py"
    )
    tree = ast.parse(script.read_text(encoding="utf-8-sig"))

    image_fields = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == "image_fields"
                for target in node.targets
            ):
                image_fields = [
                    item.elts[0].value
                    for item in node.value.elts
                ]

    assert image_fields is not None
    assert len(image_fields) == len(set(image_fields))
    assert {"desertion_no", "image_slot", "source_url"}.issubset(image_fields)
    assert set(PHOTO_FIELDS).issubset(image_fields)
