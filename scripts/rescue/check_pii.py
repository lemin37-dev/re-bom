"""data/sample 아래 JSON 에 개인정보가 남았는지 검사한다. 하나라도 나오면 종료 코드 1."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "data" / "sample"
PATTERNS = {
    "휴대전화(마스킹 안 됨)": re.compile(r"01[016789][-\s.]?\d{3,4}[-\s.]?(?!\*{4})\d{4}"),
    "주민등록번호 모양": re.compile(r"\d{6}[-\s]?[1-4]\d{6}"),
    "이메일": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "동·호수": re.compile(r"\d+\s*동\s*\d+\s*호"),
}
SKIP_KEYS = {"desertionNo", "careRegNo", "noticeNo", "rfidCd", "updTm", "happenDt", "noticeSdt", "noticeEdt"}  # 숫자열 식별번호·날짜는 전화번호·주민번호 모양과 겹친다


def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "careOwnerNm":
                yield "careOwnerNm 키가 남음", path
            if k in SKIP_KEYS or str(k).startswith("popfile") or str(k).endswith("Img"):
                continue
            yield from walk(v, k)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v, path)
    elif isinstance(o, str):
        for name, pat in PATTERNS.items():
            if pat.search(o):
                yield name, f"{path}: {o[:40]}"
        if path == "careAddr" and len(o.split()) > 2:
            yield "careAddr 가 시·도, 시·군·구보다 자세함", o[:40]


bad = 0
for f in sorted(p for p in ROOT.rglob("*.json") if p.name != "expected.json"):  # expected.json 은 공고번호 목록
    hits = list(walk(json.loads(f.read_text(encoding="utf-8"))))
    for name, where in hits[:5]:
        print(f"[{f.relative_to(ROOT.parent.parent)}] {name} -> {where}")
    bad += len(hits)
    print(f"{f.relative_to(ROOT)}: {'이상 없음' if not hits else f'{len(hits)}건 발견'}")
sys.exit(1 if bad else 0)
