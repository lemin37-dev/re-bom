import json
from collections import Counter
from pathlib import Path

from rebom.storage.silver.transform_notice import transform_notice


def main():
    path = Path("data/sample/rescue_sample.json")
    records = json.loads(path.read_text(encoding="utf-8"))

    results = []
    failures = []

    for raw in records:
        try:
            results.append(transform_notice(raw))
        except (ValueError, TypeError, KeyError) as error:
            failures.append((raw.get("desertionNo"), str(error)))

    counts = Counter(
        item["weight_parse_status"] for item in results
    )

    print("Total:", len(records))
    print("Success:", len(results))
    print("Failed:", len(failures))
    print("Weight status:", dict(counts))

    for item in results[:3]:
        print(item)

    for item in failures[:10]:
        print("ERROR:", item)

    assert len(records) == 200
    assert len(results) == 200
    assert not failures

    assert transform_notice({
        "desertionNo": "TEST001",
        "age": "2026(60일미만)(년생)",
        "weight": "2~2.5(Kg)",
        "processState": "보호중",
    })["weight_max_kg"] == 2.5


if __name__ == "__main__":
    main()
