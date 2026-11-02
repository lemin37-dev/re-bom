"""샘플 데이터 생성. 로컬 원본(수집본·스냅샷)에서 뽑아 개인정보를 처리한 사본을 data/sample/ 에 쓴다.

입력
  --t0        이전 수집본(JSON 배열). 예: 10/28 저장본 29,463건
  --snapshot  fetch_snapshot.py 결과(updated / list_notice / list_protect / full)
출력
  data/sample/rescue_sample.json                 구조동물 공고 200건 (보호중 100 + 종료 100, 원본 필드 그대로)
  data/sample/incremental/*.json                 증분 수집 시험용 쌍과 API 조회 결과, expected.json
"""
import argparse
import json
import random
from pathlib import Path

from sanitize import sanitize

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "sample"
random.seed(20261030)
ACTIVE = "보호중"


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def take(pool: list[str], n: int, used: set[str]) -> list[str]:
    cand = [k for k in pool if k not in used]
    got = random.sample(cand, min(n, len(cand)))
    used.update(got)
    return got


def photo_count(r: dict) -> int:
    return sum(1 for i in range(1, 9) if r.get(f"popfile{i}"))


def build_rescue_sample(t1: dict[str, dict]) -> list[dict]:
    """보호중 100 + 종료 100. 시험에 필요한 경우를 몇 건씩 먼저 채우고 나머지는 무작위."""
    out = []
    for active in (True, False):
        pool = [k for k, v in t1.items() if (v["processState"] == ACTIVE) == active]
        used: set[str] = set()
        quotas = [
            ("기타 축종", [k for k in pool if t1[k].get("upKindCd") == "429900"], 6),
            ("고양이", [k for k in pool if t1[k].get("upKindCd") == "422400"], 30),
            ("사진 3장 이상", [k for k in pool if photo_count(t1[k]) >= 3], 8),
            ("특징 문장 9자 이하", [k for k in pool if len(t1[k].get("specialMark", "")) <= 9], 15),
            ("체중 형식이 섞임", [k for k in pool if not str(t1[k].get("weight", "")).replace(".", "").replace("(Kg)", "").isdigit()], 6),
            ("건강·접종 체크가 있음", [k for k in pool if t1[k].get("vaccinationChk") or t1[k].get("healthChk")], 6),
            ("사회성·건강 칸이 있음", [k for k in pool if t1[k].get("sfeSoci") or t1[k].get("sfeHealth")], 4),
            ("처분사유가 있음", [k for k in pool if t1[k].get("endReason")], 0 if active else 15),
        ]
        ids = []
        for _, p, n in quotas:
            ids += take(p, n, used)
        ids += take(pool, 100 - len(ids), used)
        out += [t1[k] for k in ids[:100]]
    random.shuffle(out)
    return [sanitize(r) for r in out]


def build_incremental(t0: dict[str, dict], snap: dict) -> None:
    t1, upd = snap["full"], snap["updated"]
    lst = {**snap["list_notice"], **snap["list_protect"]}
    status_changed = [k for k in t0 if k in t1 and t0[k]["processState"] != t1[k]["processState"]]
    content_only = [k for k in t0 if k in t1 and t0[k]["processState"] == t1[k]["processState"] and t0[k] != t1[k]
                    and t0[k]["updTm"] == t1[k]["updTm"]]
    back = [k for k in status_changed if t0[k]["processState"] != ACTIVE and t1[k]["processState"] == ACTIVE]
    new = [k for k in t1 if k not in t0]
    unchanged = [k for k in t0 if k in t1 and t0[k] == t1[k]]
    used: set[str] = set()
    cats: dict[str, list[str]] = {}
    cats["복귀(종료→보호중)"] = take(back, 99, used)
    cats["상태 변경, 수정일 조회가 돌려줌"] = take([k for k in status_changed if k in upd and k not in back], 15, used)
    cats["상태 변경, 수정일 조회가 놓침"] = take([k for k in status_changed if k not in upd and k not in back], 25, used)
    cats["사진·비고 칸만 변경(updTm 그대로)"] = take(content_only, 15, used)
    cats["신규, 수정일 조회가 돌려줌(보호중)"] = take([k for k in new if k in upd and t1[k]["processState"] == ACTIVE], 15, used)
    cats["신규, 처음부터 종료"] = take([k for k in new if t1[k]["processState"] != ACTIVE], 10, used)
    cats["신규, 수정일 조회에 없음"] = take([k for k in new if k not in upd], 99, used)
    cats["변경 없음(보호중)"] = take([k for k in unchanged if t0[k]["processState"] == ACTIVE], 12, used)
    cats["변경 없음(종료)"] = take([k for k in unchanged if t0[k]["processState"] != ACTIVE], 12, used)
    cats["변경 없음(기타 축종)"] = take([k for k in unchanged if t0[k].get("upKindCd") == "429900"], 4, used)
    sel = [k for ks in cats.values() for k in ks]
    vanished = [k for k in sel if k in t0 and t0[k]["processState"] == ACTIVE and k not in lst and k not in upd]

    d = OUT / "incremental"
    dump(d / "t0_2026-10-28.json", [sanitize(t0[k]) for k in sel if k in t0])
    dump(d / "truth_2026-10-30.json", [sanitize(t1[k]) for k in sel])
    dump(d / "api_updated.json", [sanitize(upd[k]) for k in sel if k in upd])
    dump(d / "api_list_notice.json", [sanitize(snap["list_notice"][k]) for k in sel if k in snap["list_notice"]])
    dump(d / "api_list_protect.json", [sanitize(snap["list_protect"][k]) for k in sel if k in snap["list_protect"]])
    dump(d / "api_lookup.json", [sanitize(t1[k]) for k in vanished])
    dump(d / "expected.json", {c: ks for c, ks in cats.items()})
    print({c: len(ks) for c, ks in cats.items()}, "선택", len(sel), "사라진 공고", len(vanished))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--t0", required=True)
    ap.add_argument("--snapshot", required=True)
    a = ap.parse_args()
    t0 = {r["desertionNo"]: r for r in json.loads(Path(a.t0).read_text(encoding="utf-8"))}
    snap = json.loads(Path(a.snapshot).read_text(encoding="utf-8"))
    sample = build_rescue_sample(snap["full"])
    dump(OUT / "rescue_sample.json", sample)
    print("rescue_sample", len(sample))
    build_incremental(t0, snap)
