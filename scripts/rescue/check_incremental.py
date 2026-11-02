"""증분 수집 방식을 샘플에서 다시 확인한다 (API 호출 없음).

방식: 이전 수집본(t0) + ① 수정일 증분 + ② 보호중 목록 + ③ 목록에서 사라진 공고 조회 결과를 합쳐서
      같은 날 전부 다시 받은 정답(truth)과 공고 단위로 비교한다.
사용: python check_incremental.py
"""
import collections
import json
from pathlib import Path

D = Path(__file__).resolve().parents[2] / "data" / "sample" / "incremental"
ACTIVE = "보호중"


def load(name: str) -> dict[str, dict]:
    return {r["desertionNo"]: r for r in json.loads((D / name).read_text(encoding="utf-8"))}


t0, truth = load("t0_2026-10-28.json"), load("truth_2026-10-30.json")
upd, lookup = load("api_updated.json"), load("api_lookup.json")
lst = {**load("api_list_notice.json"), **load("api_list_protect.json")}
cat_of = {k: c for c, ks in json.loads((D / "expected.json").read_text(encoding="utf-8")).items() for k in ks}

only_upd = {**t0, **upd}  # ① 만 적용
merged = {**t0, **upd, **lst}  # ① + ②
vanished = [k for k, v in t0.items() if v["processState"] == ACTIVE and k not in lst and k not in upd]
merged.update({k: lookup[k] for k in vanished if k in lookup})  # ③


def report(name: str, got: dict[str, dict]) -> None:
    ids_diff = set(got) ^ set(truth)
    state_diff = [k for k in set(got) & set(truth) if got[k]["processState"] != truth[k]["processState"]]
    content_diff = [k for k in set(got) & set(truth) if got[k] != truth[k]]
    print(f"[{name}] 공고번호 차이 {len(ids_diff)} | 상태 차이 {len(state_diff)} | 내용이 다른 공고 {len(content_diff)}")
    by_cat = collections.Counter(cat_of[k] for k in content_diff)
    for c, n in by_cat.most_common():
        print(f"    - {c}: {n}")


print(f"이전 수집본 {len(t0)}건, 정답 {len(truth)}건, 사라진 공고 {len(vanished)}건(그중 조회 결과 {len(lookup)}건)")
report("① 수정일 증분만", only_upd)
report("① + ② 보호중 목록 + ③ 사라진 공고 조회", merged)
