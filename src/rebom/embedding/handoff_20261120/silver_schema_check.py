"""Silver 칼럼 점검 — 서버 Silver(Glue rebom_silver)와 로컬 Silver의 칼럼을 뽑아 팀 칼럼 정의표 · A 템플릿 규칙과 맞춰 봄

왜: 카드(A 템플릿)는 Silver 칼럼을 읽어 만듭니다. 서버와 로컬 Silver의 칼럼 이름·값 규칙이 다르면
    다른 정보가 카드에 섞이거나(예: 성별이 '수컷'으로 저장돼 카드에서 빠짐) 필터와 카드가 서로 다른 말을 합니다.

읽기만 합니다. Glue·S3·DB에 쓰지 않고, 결과는 --out 폴더에만 씁니다. 비용: S3 GET 몇십 번 (사실상 0)
값은 출력하지 않는 칼럼: 연락처·이름·주소(이름에 tel·phone·owner·charge·addr·nm 등) · 특징 글(전화번호 개수만 셈)

단계
  server   Glue <SILVER_DB>의 모든 표 → 칼럼·타입·파티션 (+ --sample-rows 행을 S3 parquet에서 읽어 값 점검)
  local    로컬 Silver (parquet · csv · jsonl 파일이나 폴더, .duckdb · .sqlite) → 같은 형식
  compare  정의표(silver_contract_20261118.csv)와 맞대기 + 서버↔로컬 차이 → silver_schema_report.md
  all      server + local + compare 한 번에

사용 예
  python silver_schema_check.py all --local D:/rebom/data/silver --out silver_check
  python silver_schema_check.py server --tables "notice|image" --sample-rows 3000 --out silver_check
  python silver_schema_check.py local /content/drive/MyDrive/rebom_out/dataset_v2/full_3y/notices_3y.parquet --out silver_check
  python silver_schema_check.py compare --pair silver_animal_notice_real_20261111_test=notices_3y --out silver_check
  (선택) --card-ref text_smoke_ref.parquet : 기준 30마리의 A1 카드가 이 Silver로 글자까지 같게 만들어지는지

환경 변수: AWS_REGION(기본 ap-northeast-2) · SILVER_DB(기본 rebom_silver) · AWS 자격 증명은 평소처럼 (값은 다루지 않음)
같은 폴더에 text_card_reference.py가 있어야 합니다 (카드 규칙의 기준).
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), os.getcwd()]
try:
    import text_card_reference as T
except ImportError:
    sys.exit("text_card_reference.py를 이 스크립트와 같은 폴더에 두세요 (카드 규칙의 기준)")

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
SILVER_DB = os.environ.get("SILVER_DB", "rebom_silver")
DEFAULT_CONTRACT = HERE / "silver_contract_20261118.csv"

# 소문자·밑줄 제거로 맞지 않는 이름 → 정의표 이름. 별칭으로 맞춘 칼럼은 보고서에 '?'(확인 필요)로 표시
ALIASES = {"breedname": "kindNm", "breednameraw": "kindNm", "kindname": "kindNm", "kindnmraw": "kindNm",
           "breedcode": "kindCd", "breedcd": "kindCd", "kindcode": "kindCd",
           "colorraw": "colorCd", "colorname": "colorCd", "color": "colorCd",
           "sexcode": "sexCd", "sex": "sexCd", "neutercode": "neuterYn", "neuter": "neuterYn", "neutered": "neuterYn",
           "ageraw": "age", "weightraw": "weight", "weightnum": "weight_kg", "kg": "weight_kg",
           "specialmarkraw": "specialMark", "sfesociraw": "sfeSoci", "sfehealthraw": "sfeHealth",
           "species": "upKindNm", "speciesname": "upKindNm", "upkindname": "upKindNm", "upkindcode": "upKindCd",
           "colortags": "color_tag", "colorfamily": "color_tag", "colornorm": "color_tag",
           "sizeband": "size", "dogsize": "size", "rfid": "rfid_yn", "hasrfid": "rfid_yn",
           "u60": "u60_at_notice", "under60days": "u60_at_notice", "birthyr": "birth_year",
           "speciescode": "upKindCd", "speciescd": "upKindCd", "speciesnm": "upKindNm"}
# 11-18 서버 Silver 이름 (사용자 확인): species_code=upKindCd · species_name=upKindNm · breed_code=kindCd · breed_name_raw=kindNm
#   neuter_code=neuterYn · weight_raw=weight. breed_full_name_raw는 kindNm이 아님('[개] 믹스견'처럼 축종이 붙은 이름일 수 있음) → 일부러 대응시키지 않음
PRIVATE_NAME = re.compile(r"tel|phone|mobile|owner|charge|callnm|callname|addr|email|manager|officer", re.I)
NOT_PRIVATE = {"kindNm", "upKindNm", "orgNm", "careNm", "breed_name_raw", "kind_nm", "up_kind_nm", "org_nm", "care_nm"}
SAFE_VALUE = {"sexCd", "neuterYn", "age", "birth_year", "u60_at_notice", "weight", "weight_kg", "size", "colorCd",
              "color_tag", "color_pattern", "kindNm", "kindCd", "is_mix", "upKindNm", "upKindCd", "processState"}
AGE_RE = re.compile(r"^\d{4}\s*(\(\s*60일\s*미만\s*\))?\s*\(년생\)$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
CODE_LIKE = re.compile(r"^\d{3,}$")
TEXT_COLS = ["specialMark", "sfeSoci", "sfeHealth", "info_text", "social_text", "health_text"]
EXPECTED_MIX = {("info_text", "specialMark"), ("info_text", "sfeSoci"), ("info_text", "sfeHealth")}   # 정의표: info_text = 사회성+건강+특징


def log(*a):
    print(*a, flush=True)


def norm(name):
    return re.sub(r"[^0-9a-z가-힣]", "", str(name).lower())


def sv(v):
    """None·NaN·pd.NA → '' · 나머지는 글자로 (배열은 그대로 돌려줌)"""
    if v is None or isinstance(v, (list, tuple, np.ndarray, dict)):
        return v if v is not None else ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v)


def blank(v):
    x = sv(v)
    return (len(x) == 0) if isinstance(x, (list, tuple, np.ndarray, dict)) else x.strip() in ("", "nan", "None", "NaN", "<NA>", "null")


def mask(x, n=48):
    return T.PHONE.sub("[번호]", str(x))[:n]


def num(v):
    try:
        f = float(sv(v))
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def as_bool(v):
    x = str(sv(v)).strip().lower()
    return True if x in ("true", "1", "y", "yes", "t") else (False if x in ("false", "0", "n", "no", "f") else None)


# ── 정의표
def load_contract(path):
    c = pd.read_csv(path, dtype=str).fillna("")
    if "칼럼" not in c.columns:
        sys.exit(f"정의표에 '칼럼' 열이 없습니다: {path}")
    return c


def matcher(contract):
    names = list(contract["칼럼"])
    by_norm = {norm(n): n for n in names}

    def match(col):
        if col in names:
            return col, "같음"
        if norm(col) in by_norm:
            return by_norm[norm(col)], "표기만 다름"
        a = ALIASES.get(norm(col))
        if a in names:
            return a, "별칭(확인 필요)"
        return "", ""
    return match


RANK = {"같음": 0, "표기만 다름": 1, "별칭(확인 필요)": 2}


def to_contract_names(df, match):
    """표 칼럼 → 정의표 이름 (같은 정의표 칼럼에 여럿이 맞으면 같음 > 표기만 다름 > 별칭 순으로 1개)"""
    best = {}
    for c in df.columns:
        k, how = match(c)
        if k and (k not in best or RANK[how] < RANK[best[k][1]]):
            best[k] = (c, how)
    return df[[c for c, _ in best.values()]].rename(columns={c: k for k, (c, _) in best.items()})


def type_norm(t):
    t = str(t).lower()
    for pat, out in [(r"array|list", "배열"), (r"struct|map|json", "구조"), (r"timestamp|datetime", "시각"), (r"^date", "날짜"),
                     (r"bool", "참거짓"), (r"double|float|decimal|real", "실수"), (r"int|long", "정수"),
                     (r"string|varchar|char|text|object|utf8|large_string|csv", "글자"), (r"파티션", "파티션")]:
        if re.search(pat, t):
            return out
    return t or "?"


# ── 털색 규칙 3가지 (어느 세대 규칙으로 만든 Silver인지 가리기 위해)
COLOR_CANON = [("흰", "흰"), ("하얀", "흰"), ("백", "흰"), ("검", "검"), ("흑", "검"), ("블랙", "검"), ("갈", "갈"), ("브라운", "갈"),
               ("노", "노"), ("황", "노"), ("회", "회"), ("그레이", "회"), ("기타", "기타")]
E25_FAMILY = {"흰": ["흰", "하얀", "하얗", "하양", "백", "화이트", "아이보리", "이이보리", "크림", "미색"], "검": ["검", "흑", "블랙"],
              "갈": ["갈", "브라운", "밤", "초코", "쵸코", "초콜", "쵸콜", "고동", "베이지"],
              "노": ["노란", "노랑", "금색", "골드", "살구", "겨자", "레몬", "황", "누렁", "치즈", "인절미"], "회": ["회", "그레이", "실버"]}
E25_BRINDLE = ["호반", "호피", "호구", "브린들", "호랑이"]


def tokens(v):
    v = sv(v)
    if isinstance(v, (list, tuple, np.ndarray)):
        items = [str(sv(x)) for x in v]
    else:
        x = str(v).strip()
        items = None
        if x.startswith("["):
            try:
                items = [str(sv(t)) for t in json.loads(x)]
            except ValueError:
                x = x.strip("[]")
        if items is None:
            items = re.split(r"\s*[,·|/&]\s*|\s+", x)
    return [t.strip().strip("'\"") for t in items if t and t.strip().strip("'\"")]


def canon_color(t):
    for k, c in COLOR_CANON:
        if t.startswith(k):
            return c
    return "?" + t


def color_template(raw):
    return {canon_color(t) for t in T.color_tag(raw)}


def color_e25(raw, is_cat):
    s2 = re.sub(r"흑갈|황갈|적갈|회갈|진갈", "갈", str(sv(raw)))
    fam = {k for k, ws in E25_FAMILY.items() if any(w in s2 for w in ws)}
    if any(w in s2 for w in E25_BRINDLE):
        fam.add("노")
    if is_cat and "삼색" in s2:
        fam |= {"흰", "검", "노"}
    if not fam and any(w in s2 for w in ["어두운", "어둡"]):
        fam.add("검")
    return fam


# ── 값 점검 (정의표 이름으로 바꾼 표본)
def value_checks(df, image=False):
    out, n = [], len(df)
    if not n:
        return out

    def add(check, target, checked, bad, examples=None, memo=""):
        out.append({"점검": check, "칼럼": target, "점검 행": int(checked), "다름": int(bad),
                    "비율": round(bad / checked, 4) if checked else None,
                    "예시": json.dumps(examples or [], ensure_ascii=False), "메모": memo})

    has = lambda *cs: all(c in df.columns for c in cs)   # noqa: E731
    is_cat = pd.Series(False, index=df.index)
    if has("upKindNm"):
        is_cat |= df["upKindNm"].map(lambda v: "고양이" in str(sv(v)))
    if has("upKindCd"):
        is_cat |= df["upKindCd"].map(lambda v: str(sv(v)) == "422400")
    if has("kindNm"):
        is_cat |= df["kindNm"].map(lambda v: str(sv(v)).endswith("묘") or "고양이" in str(sv(v)))
    dog = ~is_cat

    # 1) 코드 값 — 템플릿은 M/F · Y/N만 읽음 (다른 값이면 카드에서 그 항목이 조용히 빠짐)
    for col, ok, memo in [("sexCd", {"M", "F", "Q"}, "템플릿은 M→수컷 · F→암컷만 읽음 (다른 값이면 카드에서 성별이 빠짐)"),
                          ("neuterYn", {"Y", "N", "U"}, "템플릿은 Y·N만 읽음 (다른 값이면 카드에서 중성화가 빠짐)")]:
        if has(col):
            x = df[col].map(lambda v: str(sv(v)).strip())
            nb = x[x != ""]
            bad = nb[~nb.isin(ok)]
            add("코드 값", col, len(nb), len(bad), [{"값": k, "행": int(v)} for k, v in bad.value_counts().head(6).items()],
                memo + f" | 빈 값 {int((x == '').sum())}행")
    # 2) 나이
    if has("age"):
        a = df["age"].map(lambda v: str(sv(v)).strip())
        nb = a[a != ""]
        bad = nb[~nb.map(lambda s: bool(AGE_RE.match(s)))]
        add("원문 형식", "age", len(nb), len(bad), [{"값": mask(k, 30), "행": int(v)} for k, v in bad.value_counts().head(6).items()],
            "기대: 'YYYY(년생)' · 'YYYY(60일미만)(년생)'")
    if has("age", "birth_year"):
        exp = df["age"].map(T.birth_year)
        got = df["birth_year"].map(num)
        m = exp.notna() | got.notna()
        bad = m & (exp.map(lambda x: x if x is not None else -1) != got.map(lambda x: int(x) if x is not None else -1))
        add("규칙 일치", "birth_year", int(m.sum()), int(bad.sum()),
            [{"age": mask(df.at[i, "age"], 30), "서버/로컬": sv(df.at[i, "birth_year"]), "규칙": exp[i]} for i in df.index[bad][:5]],
            "규칙: age의 첫 네 자리 숫자")
    if has("age", "u60_at_notice"):
        exp = df["age"].map(lambda v: "60일" in str(sv(v)))
        got = df["u60_at_notice"].map(as_bool)
        bad = got.notna() & (got != exp)
        add("규칙 일치", "u60_at_notice", int(got.notna().sum()), int(bad.sum()),
            [{"age": mask(df.at[i, "age"], 30), "값": sv(df.at[i, "u60_at_notice"])} for i in df.index[bad][:5]], "규칙: age에 '60일'이 있으면 참")
    # 3) 몸무게 — 범위는 큰 값(E25·템플릿) · 가운데 값이면 옛 C 규칙
    if has("weight", "weight_kg"):
        rows, bad_ex, kinds = 0, [], {}
        for i in df.index:
            raw, got, exp = sv(df.at[i, "weight"]), num(df.at[i, "weight_kg"]), T.kg(df.at[i, "weight"])
            if exp is None and got is None:
                continue
            rows += 1
            if exp is not None and got is not None and abs(exp - got) < 1e-6:
                continue
            nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", re.sub(r"(\d),(\d)", r"\1.\2", str(raw)))]
            if got is not None and len(nums) >= 2 and abs(got - min(nums[:2])) < 1e-6:
                k = "범위 → 작은 값"
            elif got is not None and len(nums) >= 2 and abs(got - round(sum(nums[:2]) / 2, 2)) < 0.006:
                k = "범위 → 가운데 값 (옛 C 규칙)"
            elif exp is None and got is not None and nums and nums[0] > 100 and abs(got - nums[0] / 1000) < 1e-6:
                k = "100kg 초과 → ÷1000 (E25 분위수 변환)"
            elif got is None:
                k = "Silver 빈 값 · 템플릿은 읽음"
            elif exp is None:
                k = "템플릿 빈 값 · Silver는 값 있음"
            else:
                k = "다름"
            kinds[k] = kinds.get(k, 0) + 1
            if len(bad_ex) < 6:
                bad_ex.append({"weight": mask(raw, 24), "Silver": got, "템플릿": exp, "종류": k})
        add("규칙 일치", "weight_kg", rows, sum(kinds.values()), bad_ex, "종류별: " + json.dumps(kinds, ensure_ascii=False))
    # 4) 크기
    if has("size"):
        z = df["size"].map(lambda v: str(sv(v)).strip())
        vc = z.value_counts()
        odd = vc[~vc.index.isin(["소형", "중형", "대형", ""])]
        add("값 목록", "size", n, int(odd.sum()), [{"값": k, "행": int(v)} for k, v in vc.head(8).items()],
            "소형·중형·대형·빈 값 밖의 값(예: 정보 없음 · 성장 중)이 '다름' — 필터·카드에 그대로 섞이지 않게 정해야 함")
        if is_cat.any():
            cs = z[is_cat]
            add("정의표(개만)", "size", int(is_cat.sum()), int((cs != "").sum()), [{"값": k, "행": int(v)} for k, v in cs.value_counts().head(4).items()],
                "정의표: 크기는 개만. 템플릿은 고양이에도 크기를 붙임 (고양이 카드 규칙은 아직 정하지 않음)")
        if has("weight_kg"):
            exp = df["weight_kg"].map(lambda v: T.size_of(num(v)))
            m = dog & z.isin(["소형", "중형", "대형"])
            bad = m & (exp != z)
            add("규칙 일치", "size", int(m.sum()), int(bad.sum()),
                [{"weight_kg": num(df.at[i, "weight_kg"]), "Silver": z[i], "규칙": exp[i]} for i in df.index[bad][:5]],
                "규칙(Silver, 11-20): 10kg 미만 소형 · 10~25kg 미만 중형 · 25kg 이상 대형 (Silver의 weight_kg로 계산)")
    # 5) 털색 — 템플릿 · E25(정의표 원본) 중 어느 쪽과 같은지
    if has("colorCd", "color_tag"):
        rows = same_t = same_e = 0
        kinds, ex, vocab = {}, [], {}
        for i in df.index:
            raw = df.at[i, "colorCd"]
            toks = tokens(df.at[i, "color_tag"])
            for t in toks:
                vocab[t] = vocab.get(t, 0) + 1
            got = {canon_color(t) for t in toks} - {"기타"}
            tp, e25 = color_template(raw), color_e25(raw, bool(is_cat[i]))
            rows += 1
            same_t += got == tp
            same_e += got == e25
            if got != tp:
                s = str(sv(raw))
                k = ("Silver 기타(템플릿은 색 있음)" if not got else
                     "노랑을 갈색으로 (옛 C3 규칙)" if "노" in tp and "노" not in got and "갈" in got else
                     "고양이 삼색 (E25만 3색)" if is_cat[i] and "삼색" in s else
                     "호피 (템플릿은 노랑계열)" if any(w in s for w in E25_BRINDLE) else
                     "모르는 색 이름" if any(c.startswith("?") for c in got) else "다름")
                kinds[k] = kinds.get(k, 0) + 1
                if len(ex) < 6:
                    ex.append({"colorCd": mask(raw, 24), "Silver": "·".join(toks), "템플릿": "·".join(T.color_tag(raw)), "종류": k})
        add("규칙 일치(템플릿)", "color_tag", rows, rows - same_t, ex, "종류별: " + json.dumps(kinds, ensure_ascii=False)
            + " | '기타'는 빈 값으로 보고 비교")
        add("규칙 일치(E25)", "color_tag", rows, rows - same_e, [], "E25 = 정의표 원본 규칙(고양이 삼색 포함). 이쪽이 더 맞으면 서버는 E25대로 만든 것")
        add("값 목록", "color_tag", rows, sum(v for k, v in vocab.items() if canon_color(k).startswith("?")),
            [{"값": k, "행": v} for k, v in sorted(vocab.items(), key=lambda kv: -kv[1])[:10]],
            "계열 이름 표기 확인 (카드: 흰색계열 · E25: 흰색 · 정의표 글: 흰색·검정·…·노랑계열)")
    if has("color_pattern"):
        vocab = pd.Series([t for v in df["color_pattern"] for t in tokens(v)]).value_counts()
        add("값 목록", "color_pattern", n, 0, [{"값": k, "행": int(v)} for k, v in vocab.head(8).items()], "정의표: 호피·얼룩·삼색·반점·턱시도·줄무늬")
    # 6) 믹스 여부
    if has("kindNm", "is_mix"):
        kn = df["kindNm"].map(lambda v: str(sv(v)).strip())
        kc = df["kindCd"].map(lambda v: str(sv(v)).strip()) if has("kindCd") else pd.Series("", index=df.index)
        exp = kn.str.contains("믹스") | (kn == "기타") | (kc == "000115")
        got = df["is_mix"].map(as_bool)
        bad = got.notna() & (got != exp)
        bench = kn.isin(["", "미상"])
        add("규칙 일치", "is_mix", int(got.notna().sum()), int(bad.sum()),
            [{"kindNm": kn[i], "kindCd": kc[i], "is_mix": sv(df.at[i, "is_mix"])} for i in df.index[bad][:5]],
            f"정의표: '믹스' 포함 또는 기타(000115) · 벤치마크는 빈 값·미상도 믹스로 셈 ({int(bench.sum())}행)")
    if has("kindNm"):
        kn = df["kindNm"].map(lambda v: str(sv(v)).strip())
        add("섞임", "kindNm", int((kn != "").sum()), int(kn.map(lambda s: bool(CODE_LIKE.match(s))).sum()),
            [{"값": k} for k in kn[kn.map(lambda s: bool(CODE_LIKE.match(s)))].unique()[:5]], "품종 이름 칸에 코드(숫자)가 들어 있으면 카드에 숫자가 들어감")
    # 7) 글 칼럼 — 전화번호 · 관리 번호 · 다른 칼럼 글이 섞였는지 (개수만)
    if has("specialMark"):
        sm = df["specialMark"].map(lambda v: str(sv(v)))
        nb = sm[sm.str.strip() != ""]
        add("개인정보", "specialMark", len(nb), int(nb.map(lambda s: bool(T.PHONE.search(s))).sum()), [],
            "전화번호 모양이 있는 행 수 (값은 출력 안 함) → 카드 만들 때 [번호]로 가림")
        add("관리 번호", "specialMark", len(nb), int(nb.map(lambda s: bool(T.LEADING_ID.match(s))).sum()), [], "앞의 관리 번호 → 카드에서 지움")
    tc = [c for c in TEXT_COLS if has(c)]
    for i, a in enumerate(tc):
        for b in tc[i + 1:]:
            x, y = df[a].map(lambda v: str(sv(v)).strip()), df[b].map(lambda v: str(sv(v)).strip())
            both = (x != "") & (y != "")
            if not both.any():
                continue
            eq = both & (x == y)
            inc = both & ~eq & pd.Series([(len(q) >= 6 and q in p) or (len(p) >= 6 and p in q) for p, q in zip(x, y)], index=df.index)
            pair = tuple(sorted((a, b), key=lambda c: c != "info_text"))
            exp_ = pair in EXPECTED_MIX
            add("글 칼럼 섞임", f"{a} ↔ {b}", int(both.sum()), int(eq.sum() + inc.sum()), [],
                f"같음 {int(eq.sum())} · 한쪽이 다른 쪽을 포함 {int(inc.sum())}" + (" (정의표대로: info_text는 이어 붙인 글)" if exp_ else " ← 정의표에 없는 섞임"))
    if has("careTel"):
        tel = df["careTel"].map(lambda v: re.sub(r"\D", "", str(sv(v))))
        for c in tc:
            col = df[c].map(lambda v: re.sub(r"\D", "", str(sv(v))))
            hit = [(len(t) >= 8 and t in s) for t, s in zip(tel, col)]
            add("개인정보", f"careTel → {c}", int((tel.str.len() >= 8).sum()), int(sum(hit)), [], "보호소 전화번호가 글 칼럼에 들어 있는 행 (값은 출력 안 함)")
    for c in [c for c in ["info_text", "social_text", "health_text", "sfeSoci", "sfeHealth"] if has(c)]:
        s = df[c].map(lambda v: str(sv(v)))
        add("개인정보", c, int((s.str.strip() != "").sum()), int(s.map(lambda t: bool(T.PHONE.search(t))).sum()), [], "전화번호 모양이 있는 행 수")
    # 8) A1 카드: Silver에 값이 있는데 카드에서 빠진 항목 (코드 표기가 달라 조용히 빠지는 경우를 찾음)
    need = ["kindNm", "age", "sexCd", "neuterYn", "weight", "colorCd", "specialMark"]
    miss = [c for c in need if not has(c)]
    sub = (df[dog] if dog.any() else df).head(2000)
    if len(sub) and has("kindNm") and len(miss) <= 3:
        drop = {"품종": 0, "년생": 0, "성별": 0, "중성화": 0, "kg": 0, "털색": 0}
        anyd, ex = 0, []
        for r in sub.to_dict("records"):
            t = T.build_card({c: r.get(c) for c in need if c in r}, "A1")
            head = t.split(". 특징: ")[0]
            g = lambda c: str(sv(r.get(c))).strip()   # noqa: E731
            d = {"품종": g("kindNm") != "" and not head.startswith(g("kindNm")),
                 "년생": g("age") != "" and "년생" not in head,
                 "성별": g("sexCd") not in ("", "Q") and not ("수컷" in head or "암컷" in head),
                 "중성화": g("neuterYn") not in ("", "U") and "중성화" not in head,
                 "kg": g("weight") != "" and "kg" not in head,
                 "털색": g("colorCd") != "" and "털색 " not in head}
            for k, v in d.items():
                drop[k] += v
            anyd += any(d.values())
            if len(ex) < 3:
                ex.append({"앞부분": mask(head, 120)})
        add("A1 카드", "Silver 값이 카드에서 빠짐", len(sub), anyd, ex,
            ("원문 칼럼 없음: " + ", ".join(miss) + " → 이 Silver만으로는 벤치마크 카드를 만들 수 없음 | " if miss else "")
            + "항목별: " + json.dumps(drop, ensure_ascii=False) + " (kg = 몸무게를 못 읽음·100kg 초과 · 털색 = 털색 사전에 없는 표기)")
    # 9) 키 중복
    if has("desertionNo"):
        k = df["desertionNo"].map(lambda v: str(sv(v)))
        if not image:
            add("키 중복", "desertionNo", n, int(n - k.nunique()), [], "공고 표인데 같은 구조번호가 여러 행 (버전·스냅숏이 섞였을 수 있음 · 표본 기준)")
    for col in ["upKindNm", "processState"]:
        if has(col):
            vc = df[col].map(lambda v: str(sv(v))).value_counts()
            add("값 목록", col, n, 0, [{"값": k, "행": int(v)} for k, v in vc.head(8).items()])
    return out


INFO_COL = re.compile(r"(status|_code$|_group$|warning|reason|_note$|normalized|_yn$|_date$)", re.I)
DETAIL_ADDR = re.compile(r"\d+\s*-\s*\d+|\d+\s*번지|(?:로|길)\s*\d+")
RFID_RE = re.compile(r"(?<!\d)(?:\d{15}|\d{3}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4})(?!\d)")


def raw_checks(df, match):
    """정의표에 없는 서버 칼럼의 뜻을 값으로 확인 (원래 이름 그대로 봄)"""
    out = []
    col = lambda *ns: next((c for c in df.columns if norm(c) in ns), None)   # noqa: E731

    def add(check, target, checked, bad, examples=None, memo=""):
        out.append({"점검": check, "칼럼": target, "점검 행": int(checked), "다름": int(bad),
                    "비율": round(bad / checked, 4) if checked else None,
                    "예시": json.dumps(examples or [], ensure_ascii=False), "메모": memo})
    # 1) 상태·코드·메모 칼럼 값 목록 (이름만으로 뜻을 모르는 칼럼)
    for c in df.columns:
        if INFO_COL.search(c) and not PRIVATE_NAME.search(c) and match(c)[1] in ("", "별칭(확인 필요)"):
            x = df[c].map(lambda v: mask(sv(v), 30) if not isinstance(sv(v), (list, tuple, np.ndarray, dict)) else mask(json.dumps(list(sv(v)), ensure_ascii=False), 30))
            vc = x.value_counts()
            add("값 목록 (뜻 확인용)", c, len(x), 0, [{"값": k, "행": int(v)} for k, v in vc.head(10).items()],
                f"서로 다른 값 {len(vc)}개 · 빈 값 {int((x.str.strip() == '').sum())}행")
    # 2) 몸무게 최소·최대 → weight_kg가 어느 쪽인지 · 경계값(10kg·25kg) 행 수
    wk, wmin, wmax = col("weightkg"), col("weightminkg", "minweightkg"), col("weightmaxkg", "maxweightkg")
    if wk and wmin and wmax:
        k, lo, hi = (df[c].map(num) for c in (wk, wmin, wmax))
        rng = lo.notna() & hi.notna() & (lo != hi)
        kinds = {"큰 값": int((rng & ((k - hi).abs() < 1e-6)).sum()), "작은 값": int((rng & ((k - lo).abs() < 1e-6)).sum()),
                 "가운데 값": int((rng & ((k - (lo + hi) / 2).abs() < 0.006)).sum())}
        add("범위 규칙", f"{wk} vs {wmin}·{wmax}", int(rng.sum()), int(rng.sum()) - kinds["큰 값"], [],
            "범위가 있는 행에서 weight_kg가: " + json.dumps(kinds, ensure_ascii=False) + " (정의표·템플릿 = 큰 값)")
    if wk:
        k = df[wk].map(num)
        add("경계값", wk, int(k.notna().sum()), int(((k - 10).abs() < 1e-9).sum()), [],
            f"정확히 10kg {int(((k - 10).abs() < 1e-9).sum())}행 · 정확히 25kg {int(((k - 25).abs() < 1e-9).sum())}행 "
            "(10kg를 소형/중형 어디에 넣을지에 따라 이 행들의 크기가 바뀜)")
    # 3) age_note ↔ u60_at_notice
    an, u6 = col("agenote"), col("u60atnotice", "u60")
    if an and u6:
        a = df[an].map(lambda v: "60일" in str(sv(v)))
        u = df[u6].map(as_bool)
        both = u.notna()
        add("중복 확인", f"{an} ↔ {u6}", int(both.sum()), int((both & (a != u)).sum()), [],
            f"age_note에 '60일'이 있는 것과 u60_at_notice가 다른 행 · '60일' 말고 다른 메모가 있는 행 "
            f"{int(df[an].map(lambda v: str(sv(v)).strip() not in ('',) and '60일' not in str(sv(v))).sum())}")
    # 4) 품종 이름 두 개
    bf, bn = col("breedfullnameraw", "breedfullname"), col("breednameraw", "kindnm")
    if bf and bn:
        f_, n_ = df[bf].map(lambda v: str(sv(v)).strip()), df[bn].map(lambda v: str(sv(v)).strip())
        diff = (f_ != n_) & (f_ != "")
        add("두 이름 비교", f"{bf} vs {bn}", int((f_ != "").sum()), int(diff.sum()),
            [{"full": mask(a, 30), "name": mask(b, 30)} for a, b in list(zip(f_[diff], n_[diff]))[:5]],
            "다르면 카드에는 벤치마크와 같은 쪽(축종 접두어 없는 이름)을 씀")
    # 4b) 발견 장소에 지번·번지·도로명 번호가 있는지 (값은 출력 안 함 · 화면에는 읍·면·동/구까지만)
    hp = col("happenplace")
    if hp:
        t = df[hp].map(lambda v: str(sv(v)).strip())
        nb = t[t != ""]
        hit = nb.map(lambda x: bool(DETAIL_ADDR.search(x)))
        add("개인정보", f"{hp} 세부 주소 모양", len(nb), int(hit.sum()), [],
            "지번(136-57) · 번지 · 도로명 번호(○○로 12)가 있는 행 수. 화면·API에는 읍·면·동/구까지만, 원문은 내부 계산용으로만")
    # 5) 동물등록번호 (15자리) — 지금 전화번호 가림 규칙은 일부만 가림
    for c in df.columns:
        if norm(c) in ("specialmark", "infotext", "sfesoci", "etcbigo"):
            t = df[c].map(lambda v: str(sv(v)))
            hit = t.map(lambda x: bool(RFID_RE.search(x)))
            add("개인정보", f"{c} 동물등록번호 모양", int((t.str.strip() != "").sum()), int(hit.sum()), [],
                "15자리 번호 행 수 (값은 출력 안 함). 지금 카드 규칙은 이 번호를 '41[번호]78'처럼 일부만 가림 → 가림 규칙 [결정 필요]")
            ry = col("rfidyn")
            if ry:
                r = df[ry].map(as_bool)
                add("출처 확인", f"{ry} ↔ {c} 번호", int(r.notna().sum()), int((r.notna() & (r != hit)).sum()), [],
                    f"rfid_yn 참 {int((r == True).sum())}행 · 글에 번호 있음 {int(hit.sum())}행 (정의표: API rfidCd에서 만듦)")   # noqa: E712
    return out


def image_checks(df):
    out = []
    if "sha256" not in df.columns:
        return out
    s = df["sha256"].map(lambda v: str(sv(v)).lower())
    out.append({"점검": "형식", "칼럼": "sha256", "점검 행": len(s), "다름": int((~s.map(lambda x: bool(SHA_RE.match(x)))).sum()),
                "비율": None, "예시": "[]", "메모": "64자리 16진수"})
    slot = next((c for c in ["slot", "image_slot", "popfile_slot"] if c in df.columns), None)
    no = next((c for c in df.columns if norm(c) == "desertionno"), None)
    if slot and no:
        d = int(df.duplicated([no, slot]).sum())
        out.append({"점검": "키 중복", "칼럼": f"{no}+{slot}", "점검 행": len(df), "다름": d, "비율": round(d / len(df), 4), "예시": "[]",
                    "메모": "같은 공고·같은 슬롯이 여러 행 → 버전 파일(_v002)이면 최신만 남겨야 함"})
    key = next((c for c in df.columns if "s3key" in norm(c) or norm(c) in ("originals3key", "imagekey")), None)
    if key:
        k = df[key].map(lambda v: str(sv(v)))
        bad = int((~k.map(lambda x: bool(re.search(r"/popfile\d+(_v\d+)?\.[A-Za-z0-9]+$", x)))).sum())
        out.append({"점검": "형식", "칼럼": key, "점검 행": len(k), "다름": bad, "비율": round(bad / len(k), 4) if len(k) else None,
                    "예시": "[]", "메모": "<공고번호>/popfile<N>[_vNNN].<확장자>"})
    return out


# ── 읽기: 서버 (Glue + S3)
_clients = {}


def aws(name):
    if name not in _clients:
        import boto3
        _clients[name] = boto3.client(name, region_name=REGION)
    return _clients[name]


def path_partitions(rel):
    return [tuple(seg.split("=", 1)) for seg in rel.split("/")[:-1] if "=" in seg]


def read_rows(items, reader, n, ids, id_col_norm="desertionno", max_files=400):
    """최근 파일부터 n행 표본 + (ids가 있으면) 그 구조번호 행을 끝까지 찾아 붙임"""
    frames, got, idrows, used = [], 0, [], 0
    for it in items[:max_files]:
        if got >= n and not ids:
            break
        df = reader(it)
        used += 1
        if got < n:
            frames.append(df)
            got += len(df)
        if ids:
            c = next((c for c in df.columns if norm(c) == id_col_norm), None)
            if c is not None:
                idrows.append(df[df[c].astype(str).isin(ids)])
    s = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(s) > n:
        s = s.sample(n, random_state=0).reset_index(drop=True)
    r = pd.concat(idrows, ignore_index=True) if idrows else pd.DataFrame()
    return s, r, used


def server_datasets(db, table_re, n, ids):
    for page in aws("glue").get_paginator("get_tables").paginate(DatabaseName=db):
        for t in page["TableList"]:
            if table_re and not re.search(table_re, t["Name"]):
                continue
            sd = t.get("StorageDescriptor", {}) or {}
            cols = [(c["Name"], c.get("Type", ""), False) for c in sd.get("Columns", [])]
            cols += [(c["Name"], c.get("Type", ""), True) for c in t.get("PartitionKeys", [])]
            loc = sd.get("Location", "")
            ds = {"source": "서버", "table": t["Name"], "location": loc, "columns": cols,
                  "kind": (t.get("Parameters") or {}).get("table_type", t.get("TableType", "")),
                  "updated": str(t.get("UpdateTime") or t.get("CreateTime") or "")[:19]}
            if (n or ids) and loc.startswith(("s3://", "s3a://")):
                try:
                    b, _, pre = re.sub(r"^s3a?://", "", loc).partition("/")
                    pre = (pre.rstrip("/") + "/") if pre else ""
                    objs = []
                    for pg in aws("s3").get_paginator("list_objects_v2").paginate(Bucket=b, Prefix=pre):
                        objs += [o for o in pg.get("Contents", []) if o["Key"].endswith(".parquet") and "/metadata/" not in o["Key"]
                                 and o["Size"] < 300e6]
                    objs.sort(key=lambda o: o["LastModified"], reverse=True)

                    def rd(o, b=b, pre=pre):
                        df = pd.read_parquet(io.BytesIO(aws("s3").get_object(Bucket=b, Key=o["Key"])["Body"].read()))
                        for k, v in path_partitions(o["Key"][len(pre):]):
                            if k not in df.columns:
                                df[k] = v
                        return df
                    ds["sample"], ds["ref_rows"], used = read_rows(objs, rd, n, ids)
                    ds["sample_note"] = f"최근 parquet {used}/{len(objs)}개에서 표본 {len(ds['sample']):,}행 (Iceberg면 지워진 행이 섞일 수 있음)"
                except Exception as e:
                    ds["sample_note"] = f"표본 읽기 실패: {type(e).__name__}: {str(e)[:150]}"
            yield ds


# ── 읽기: 로컬
SCAN_EXT = (".parquet", ".pq", ".csv", ".csv.gz", ".tsv", ".jsonl")
FILE_EXT = SCAN_EXT + (".json",)
DB_EXT = (".duckdb", ".db", ".sqlite", ".sqlite3")
SKIP_DIR = re.compile(r"^[._]|^metadata$|^__pycache__$|^_delta_log$")
PART_FILE = re.compile(r"^(part[-_]|data[-_]|\d{5}-|[0-9a-f]{8}-[0-9a-f]{4}-)", re.I)


def read_file(f, nrows=None):
    s = f.name.lower()
    if s.endswith((".parquet", ".pq")):
        df = pd.read_parquet(f)
        return df.head(nrows) if nrows else df
    if s.endswith((".csv", ".csv.gz", ".tsv")):
        for enc in ("utf-8-sig", "cp949"):
            try:
                return pd.read_csv(f, sep="\t" if ".tsv" in s else ",", nrows=nrows, dtype=str, encoding=enc, low_memory=False)
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError("utf-8/cp949", b"", 0, 1, str(f))
    if s.endswith(".jsonl"):
        return pd.read_json(f, lines=True, nrows=nrows)
    return pd.read_json(f).head(nrows) if nrows else pd.read_json(f)


def schema_of(f):
    if f.name.lower().endswith((".parquet", ".pq")):
        import pyarrow.parquet as pq
        sc = pq.read_schema(f)
        return [(n, str(sc.field(n).type)) for n in sc.names if n != "__index_level_0__"]
    df = read_file(f, 200)
    return [(c, "csv(글)" if f.name.lower().endswith((".csv", ".csv.gz", ".tsv")) else str(df[c].dtype)) for c in df.columns]


def local_datasets(paths, n, ids):
    for p in map(Path, paths):
        if not p.exists():
            log(f"   없음: {p}")
            continue
        if p.is_file() and p.name.lower().endswith(DB_EXT):
            yield from db_datasets(p, n, ids)
            continue
        if p.is_file():
            files, root = [p], p.parent
        else:
            files = [f for f in p.rglob("*") if f.is_file() and f.name.lower().endswith(SCAN_EXT)
                     and not any(SKIP_DIR.match(s) for s in f.relative_to(p).parts[:-1])]
            root = p
        groups = {}
        for f in files:
            rel = f.relative_to(root)
            dirs = [s for s in rel.parts[:-1] if "=" not in s]
            grouped = bool(PART_FILE.match(f.name)) or any("=" in s for s in rel.parts[:-1])
            stem = re.sub(r"\.(parquet|pq|csv|csv\.gz|tsv|jsonl|json)$", "", f.name, flags=re.I)
            key = "/".join(dirs if grouped else dirs + [stem]) or (p.name if p.is_dir() else stem)
            groups.setdefault(key, []).append(f)
        for name, fs in sorted(groups.items()):
            fs.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            cols, seen = [], set()
            for f in fs[:5]:
                for c, t in schema_of(f):
                    if c not in seen:
                        seen.add(c)
                        cols.append((c, t, False))
            for k, _ in path_partitions(str(fs[0].relative_to(root)).replace(os.sep, "/")):
                if k not in seen:
                    cols.append((k, "파티션", True))
            ds = {"source": "로컬", "table": name, "location": str(p), "columns": cols, "kind": f"파일 {len(fs)}개",
                  "updated": dt.datetime.fromtimestamp(fs[0].stat().st_mtime).isoformat(timespec="seconds")}
            if n or ids:
                def rd(f, root=root):
                    df = read_file(f)
                    for k, v in path_partitions(str(f.relative_to(root)).replace(os.sep, "/")):
                        if k not in df.columns:
                            df[k] = v
                    return df
                try:
                    ds["sample"], ds["ref_rows"], used = read_rows(fs, rd, n, ids)
                    ds["sample_note"] = f"최근 파일 {used}/{len(fs)}개에서 표본 {len(ds['sample']):,}행"
                except Exception as e:
                    ds["sample_note"] = f"표본 읽기 실패: {type(e).__name__}: {str(e)[:150]}"
            yield ds


def db_datasets(p, n, ids):
    if p.name.lower().endswith(".duckdb"):
        try:
            import duckdb
        except ImportError:
            log(f"   duckdb가 없어 건너뜀: {p} (pip install duckdb)")
            return
        con = duckdb.connect(str(p), read_only=True)
        tabs = con.execute("select table_schema, table_name from information_schema.tables").fetchall()
        for sch, tb in tabs:
            cols = con.execute("select column_name, data_type from information_schema.columns where table_schema=? and table_name=? "
                               "order by ordinal_position", [sch, tb]).fetchall()
            ds = {"source": "로컬", "table": f"{p.stem}.{tb}", "location": str(p), "columns": [(c, t, False) for c, t in cols],
                  "kind": "duckdb", "updated": ""}
            if n:
                ds["sample"] = con.execute(f'select * from "{sch}"."{tb}" using sample {int(n)} rows').df()
                ds["sample_note"] = f"표본 {len(ds['sample']):,}행"
            yield ds
        return
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    for (tb,) in con.execute("select name from sqlite_master where type in ('table','view')").fetchall():
        cols = [(r[1], r[2], False) for r in con.execute(f'pragma table_info("{tb}")').fetchall()]
        ds = {"source": "로컬", "table": f"{p.stem}.{tb}", "location": str(p), "columns": cols, "kind": "sqlite", "updated": ""}
        if n:
            ds["sample"] = pd.read_sql(f'select * from "{tb}" order by random() limit {int(n)}', con)
            ds["sample_note"] = f"표본 {len(ds['sample']):,}행"
        yield ds


# ── 기준 30마리 카드 일치 (선택)
def card_parity(ds, ref, match):
    rows = ds.get("ref_rows")
    if rows is None or not len(rows):
        return []
    r = to_contract_names(rows, match)
    need = ["kindNm", "age", "sexCd", "neuterYn", "weight", "colorCd", "specialMark"]
    if "desertionNo" not in r.columns or "kindNm" not in r.columns or sum(c in r.columns for c in need) < 4:
        return []
    r["desertionNo"] = r["desertionNo"].astype(str)
    if "updTm" in r.columns:
        r = r.sort_values("updTm", ascending=False)
    r = r.drop_duplicates("desertionNo")
    same, ex, found = 0, [], 0
    for _, x in ref.iterrows():
        hit = r[r["desertionNo"] == str(x["desertionNo"])]
        if hit.empty:
            continue
        found += 1
        row = {c: hit.iloc[0][c] for c in need if c in hit.columns}
        card = T.build_card(row, "A1")
        if card == x["text"]:
            same += 1
        elif len(ex) < 5:
            a, b = x["text"].split(", "), card.split(", ")
            diff = [(p, q) for p, q in zip(a + [""] * (len(b) - len(a)), b + [""] * (len(a) - len(b))) if p != q][:2]
            ex.append({"구조번호": str(x["desertionNo"]), "기준 → Silver": [[mask(p, 40), mask(q, 40)] for p, q in diff]})
    return [{"점검": "A1 카드 일치(기준 30마리)", "칼럼": "카드 글", "점검 행": found, "다름": found - same,
             "비율": round((found - same) / found, 4) if found else None, "예시": json.dumps(ex, ensure_ascii=False),
             "메모": f"기준 {len(ref)}마리 중 이 표에서 찾은 {found}마리 · 다르면 칼럼 매핑 또는 공고 내용 수정(updTm) 확인"}]


# ── 덤프
def dump(source_tag, datasets, out, contract, ref):
    match = matcher(contract)
    srows, vrows = [], []
    for ds in datasets:
        log(f"   {ds['table']}: 칼럼 {len(ds['columns'])}개 · {ds.get('sample_note', '값 점검 안 함')}")
        for i, (c, t, part) in enumerate(ds["columns"], 1):
            k, how = match(c)
            srows.append({"source": ds["source"], "table": ds["table"], "position": i, "column": c, "type": t, "type_norm": type_norm(t),
                          "partition": part, "contract_col": k, "match": how, "private_name": bool(PRIVATE_NAME.search(c)) and c not in NOT_PRIVATE,
                          "location": re.sub(r"^(s3a?://)[^/]+", r"\1<버킷>", ds["location"]), "kind": ds["kind"], "updated": ds["updated"],
                          "sample_note": ds.get("sample_note", "")})
        smp = ds.get("sample")
        if smp is not None and len(smp):
            res = value_checks(to_contract_names(smp, match), image="sha256" in smp.columns) + raw_checks(smp, match) + image_checks(smp)
            if ref is not None:
                res += card_parity(ds, ref, match)
            for r in res:
                vrows.append({"source": ds["source"], "table": ds["table"], **r})
    s = pd.DataFrame(srows)
    v = pd.DataFrame(vrows)
    s.to_csv(out / f"schema_{source_tag}.csv", index=False, encoding="utf-8-sig")
    v.to_csv(out / f"values_{source_tag}.csv", index=False, encoding="utf-8-sig")
    log(f"   저장: {out / f'schema_{source_tag}.csv'} ({len(s)}행) · {out / f'values_{source_tag}.csv'} ({len(v)}행)")


# ── 비교 보고서
def md_table(df):
    if df is None or not len(df):
        return "_(없음)_\n"
    cols = list(df.columns)
    esc = lambda x: str("" if x is None or (isinstance(x, float) and np.isnan(x)) else x).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    lines = ["| " + " | ".join(map(esc, cols)) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(esc(r[c]) for c in cols) + " |" for _, r in df.iterrows()]
    return "\n".join(lines) + "\n"


def auto_pairs(schema, manual):
    pairs = []
    for m in manual or []:
        a, _, b = m.partition("=")
        pairs.append((a.strip(), b.strip(), "지정"))
    if pairs:
        return pairs
    key = lambda r: r["contract_col"] or norm(r["column"])   # noqa: E731
    sets = {(src, tb): set(g.apply(key, axis=1)) for (src, tb), g in schema.groupby(["source", "table"])}
    srv = [k for k in sets if k[0] == "서버"]
    for loc in [k for k in sets if k[0] == "로컬"]:
        best = max(srv, key=lambda s: len(sets[s] & sets[loc]) / max(1, len(sets[s] | sets[loc])), default=None)
        if best:
            j = len(sets[best] & sets[loc]) / max(1, len(sets[best] | sets[loc]))
            if j >= 0.25:
                pairs.append((best[1], loc[1], f"자동 (칼럼 겹침 {j:.0%})"))
    return pairs


def compare(out, contract_path, manual_pairs):
    contract = load_contract(contract_path)
    sch = [pd.read_csv(f, dtype=str).fillna("") for f in [out / "schema_서버.csv", out / "schema_로컬.csv"] if f.exists()]
    if not sch:
        sys.exit(f"{out}에 schema_서버.csv · schema_로컬.csv가 없습니다. server 또는 local을 먼저 실행하세요")
    schema = pd.concat(sch, ignore_index=True)
    schema = schema[schema["column"] != ""]
    vals = [pd.read_csv(f, dtype=str).fillna("") for f in [out / "values_서버.csv", out / "values_로컬.csv"] if f.exists() and f.stat().st_size > 5]
    values = pd.concat(vals, ignore_index=True) if vals else pd.DataFrame()
    tables = schema[["source", "table", "kind", "updated", "location", "sample_note"]].drop_duplicates(["source", "table"])
    label = {(r["source"], r["table"]): f"{r['source']}:{r['table']}" for _, r in tables.iterrows()}
    mark = {"같음": "✓", "표기만 다름": "≈", "별칭(확인 필요)": "?"}

    # ① 정의표 × 표
    mat = []
    for _, c in contract.iterrows():
        row = {"묶음": c.get("묶음", ""), "정의표 칼럼": c["칼럼"], "카드": c.get("카드(A 템플릿)", "")}
        for k, lb in label.items():
            g = schema[(schema["source"] == k[0]) & (schema["table"] == k[1]) & (schema["contract_col"] == c["칼럼"])]
            row[lb] = " / ".join(f"{mark.get(r['match'], '')} {r['column']}".strip() if r["match"] != "같음" else "✓" for _, r in g.iterrows()) or "—"
        mat.append(row)
    mat = pd.DataFrame(mat)
    mat.to_csv(out / "contract_matrix.csv", index=False, encoding="utf-8-sig")

    # ② 정의표에 없는 칼럼 · 개인정보 이름
    extra = schema[schema["contract_col"] == ""].groupby(["source", "table"])["column"].apply(lambda s: ", ".join(s)).reset_index()
    priv = schema[(schema["private_name"] == "True")].groupby(["source", "table"])["column"].apply(lambda s: ", ".join(s)).reset_index()

    # ③ 서버 ↔ 로컬
    pairs = auto_pairs(schema, manual_pairs)
    diffs = []
    for st, lt, how in pairs:
        a = schema[(schema["source"] == "서버") & (schema["table"] == st)]
        b = schema[(schema["source"] == "로컬") & (schema["table"] == lt)]
        if a.empty or b.empty:
            diffs.append({"짝": f"{st} ↔ {lt}", "정하는 법": how, "종류": "표 없음", "칼럼": "", "서버": "", "로컬": ""})
            continue
        ka = {(r["contract_col"] or "~" + norm(r["column"])): r for _, r in a.iterrows()}
        kb = {(r["contract_col"] or "~" + norm(r["column"])): r for _, r in b.iterrows()}
        for k in sorted(set(ka) | set(kb), key=lambda k: (k.startswith("~"), k)):
            ra, rb = ka.get(k), kb.get(k)
            name = k.lstrip("~") if k.startswith("~") else k
            base = {"짝": f"{st} ↔ {lt}", "정하는 법": how, "칼럼": name + ("" if not k.startswith("~") else " (정의표 밖)")}
            if ra is None or rb is None:
                diffs.append({**base, "종류": "한쪽에만 있음", "서버": ra["column"] if ra is not None else "—", "로컬": rb["column"] if rb is not None else "—"})
            elif ra["column"] != rb["column"]:
                diffs.append({**base, "종류": "이름 다름", "서버": ra["column"], "로컬": rb["column"]})
            if ra is not None and rb is not None and ra["type_norm"] != rb["type_norm"] and "csv" not in str(rb["type"]) + str(ra["type"]):
                diffs.append({**base, "종류": "타입 다름", "서버": f"{ra['column']}: {ra['type']}", "로컬": f"{rb['column']}: {rb['type']}"})
    diffs = pd.DataFrame(diffs)
    diffs.to_csv(out / "server_vs_local.csv", index=False, encoding="utf-8-sig")

    # ④ 값 점검 나란히
    vside = pd.DataFrame()
    if len(values) and pairs:
        rows = []
        for st, lt, _ in pairs:
            va = values[(values["source"] == "서버") & (values["table"] == st)]
            vb = values[(values["source"] == "로컬") & (values["table"] == lt)]
            for key in pd.concat([va, vb])[["점검", "칼럼"]].drop_duplicates().itertuples(index=False):
                x = va[(va["점검"] == key[0]) & (va["칼럼"] == key[1])]
                y = vb[(vb["점검"] == key[0]) & (vb["칼럼"] == key[1])]
                f = lambda z: f"{z.iloc[0]['다름']}/{z.iloc[0]['점검 행']}" if len(z) else "—"   # noqa: E731
                rows.append({"짝": f"{st} ↔ {lt}", "점검": key[0], "칼럼": key[1], "서버 다름/점검": f(x), "로컬 다름/점검": f(y)})
        vside = pd.DataFrame(rows)

    # ⑤ 눈에 띄는 것 [추론]
    notes = []
    srv = schema[schema["source"] == "서버"]
    if len(srv) and (srv["match"] == "표기만 다름").mean() > 0.3:
        notes.append("서버 칼럼 이름이 정의표(camelCase)와 표기만 다릅니다(snake_case). 카드 코드는 이름을 바꿔 읽어야 하므로 **어느 쪽을 표준으로 할지** 정해야 합니다.")
    if (schema["match"] == "별칭(확인 필요)").any():
        g = schema[schema["match"] == "별칭(확인 필요)"]
        notes.append("별칭으로 맞춘 칼럼(확인 필요): " + ", ".join(sorted({f"{r['column']}→{r['contract_col']}" for _, r in g.iterrows()})))
    if len(values):
        v = values.copy()
        v["다름"] = pd.to_numeric(v["다름"], errors="coerce").fillna(0)
        for _, r in v[(v["다름"] > 0)].iterrows():
            key = (r["점검"], r["칼럼"])
            if key[0] == "코드 값":
                notes.append(f"{r['source']}:{r['table']} · {key[1]}에 코드가 아닌 값 {int(r['다름'])}행 → {r['메모'].split(' | ')[0]}")
            elif key == ("규칙 일치", "weight_kg"):
                notes.append(f"{r['source']}:{r['table']} · weight_kg가 템플릿 규칙과 다른 행 {int(r['다름'])} ({r['메모']}). 카드는 원문 weight에서 다시 계산하므로 카드에는 영향 없음, 필터·화면 값과 카드 값이 달라짐")
            elif key[0] == "규칙 일치(템플릿)":
                notes.append(f"{r['source']}:{r['table']} · color_tag가 템플릿 털색과 다른 행 {int(r['다름'])} ({r['메모'].split(' | ')[0]}). 털색 필터와 카드 '털색 …계열'이 다른 말을 할 수 있음")
            elif key == ("값 목록", "size"):
                notes.append(f"{r['source']}:{r['table']} · size에 소형·중형·대형 밖의 값 {int(r['다름'])}행 (예: 정보 없음·성장 중). 필터의 '미상' 처리와 맞춰야 함")
            elif key[0] == "글 칼럼 섞임" and "정의표에 없는" in r["메모"]:
                notes.append(f"{r['source']}:{r['table']} · {key[1]} 글이 섞인 행 {int(r['다름'])} ({r['메모'].split(' ←')[0]})")
            elif key[0] == "개인정보" and key[1].startswith("careTel"):
                notes.append(f"{r['source']}:{r['table']} · 보호소 전화번호가 {key[1].split('→ ')[1]}에 들어 있는 행 {int(r['다름'])} → 카드·태깅에 쓰는 글이면 보내기 전에 가려야 함")
            elif key[0] == "A1 카드":
                notes.append(f"{r['source']}:{r['table']} · Silver에 값이 있는데 카드에서 빠진 공고 {int(r['다름'])}/{r['점검 행']} ({r['메모'].split(' (kg')[0]})")
            elif key == ("규칙 일치", "size"):
                notes.append(f"{r['source']}:{r['table']} · size가 Silver weight_kg 기준 크기 규칙과 다른 행 {int(r['다름'])} → 예시 확인")
            elif key[0] == "섞임":
                notes.append(f"{r['source']}:{r['table']} · kindNm에 숫자 코드 {int(r['다름'])}행 → 매핑 확인")
            elif key[0].startswith("A1 카드 일치"):
                notes.append(f"{r['source']}:{r['table']} · 기준 카드와 다른 공고 {int(r['다름'])}/{r['점검 행']} → 예시 확인")
    if len(priv):
        notes.append("이름으로 보아 연락처·이름·주소일 수 있는 칼럼(값은 출력하지 않음 · careTel은 글 칼럼 섞임 확인에만 씀): " + "; ".join(f"{r['source']}:{r['table']} → {r['column']}" for _, r in priv.iterrows()))

    # 보고서
    now = dt.datetime.now().isoformat(timespec="seconds")
    md = [f"# Silver 칼럼 점검 ({now})\n",
          f"정의표: `{Path(contract_path).name}` · 표 {len(tables)}개 (서버 {int((tables['source'] == '서버').sum())} · 로컬 {int((tables['source'] == '로컬').sum())})\n",
          "표시: ✓ 이름 같음 · ≈ 표기만 다름(camel/snake) · ? 별칭으로 맞춤(확인 필요) · — 없음\n",
          "## 1. 표 목록 [실측]\n", md_table(tables.rename(columns={"source": "어디", "table": "표", "kind": "종류", "updated": "갱신", "location": "위치", "sample_note": "값 표본"}))]
    for (src, tb), g in schema.groupby(["source", "table"], sort=False):
        md.append(f"\n**{src}:{tb}** 칼럼 {len(g)}개: " + ", ".join(f"`{r['column']}`" + (f"({r['type']})" if r["type"] else "") for _, r in g.iterrows()) + "\n")
    md += ["\n## 2. 정의표 × 표 [실측]\n", md_table(mat),
           "\n## 3. 정의표에 없는 칼럼 [실측]\n", md_table(extra.rename(columns={"source": "어디", "table": "표", "column": "칼럼"})),
           "\n## 4. 서버 ↔ 로컬 차이 [실측 · 짝은 자동이면 추정]\n",
           "짝: " + ("; ".join(f"{a} ↔ {b} ({h})" for a, b, h in pairs) if pairs else "_(짝 없음 — --pair 서버표=로컬표로 지정)_") + "\n\n",
           md_table(diffs[[c for c in ["짝", "종류", "칼럼", "서버", "로컬"] if c in diffs.columns]] if len(diffs) else diffs),
           "\n## 5. 값 점검 — 템플릿 규칙과 같은가 [실측 · 표본]\n"]
    if len(values):
        md.append(md_table(values[["source", "table", "점검", "칼럼", "점검 행", "다름", "비율", "메모"]].rename(columns={"source": "어디", "table": "표"})))
        ex = values[values["예시"].str.len() > 2]
        if len(ex):
            md.append("\n<details><summary>예시 (전화번호 가림 · 연락처 칼럼 값은 없음)</summary>\n\n")
            for _, r in ex.iterrows():
                md.append(f"- {r['source']}:{r['table']} · {r['점검']} · {r['칼럼']}: {r['예시']}\n")
            md.append("\n</details>\n")
    else:
        md.append("_(값 점검 안 함 — --sample-rows를 0보다 크게)_\n")
    if len(vside):
        md += ["\n### 서버·로컬 나란히\n", md_table(vside)]
    md += ["\n## 6. 눈에 띄는 것 [추론 — 정할 것은 사용자·담당자]\n"] + [f"- {x}\n" for x in notes or ["(자동으로 찾은 것 없음)"]]
    (out / "silver_schema_report.md").write_text("".join(md), encoding="utf-8")
    log(f"저장: {out / 'silver_schema_report.md'} · contract_matrix.csv · server_vs_local.csv")
    for x in notes[:12]:
        log("  -", x)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["server", "local", "compare", "all"])
    ap.add_argument("paths", nargs="*", help="local: Silver 파일·폴더·.duckdb·.sqlite")
    ap.add_argument("--local", nargs="*", default=[], help="all: 로컬 Silver 경로")
    ap.add_argument("--db", default=SILVER_DB)
    ap.add_argument("--tables", default="", help="서버 표 이름 정규식 (예: 'notice|image')")
    ap.add_argument("--sample-rows", type=int, default=3000, help="값 점검 표본 행 (0 = 칼럼만)")
    ap.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    ap.add_argument("--card-ref", default="", help="text_smoke_ref.parquet (기준 30마리 A1 카드 일치)")
    ap.add_argument("--pair", action="append", default=[], help="서버표=로컬표 (여러 번 가능)")
    ap.add_argument("--out", default="silver_check")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    contract = load_contract(a.contract)
    ref = pd.read_parquet(a.card_ref) if a.card_ref else None
    ids = set(ref["desertionNo"].astype(str)) if ref is not None else set()
    if a.cmd in ("server", "all"):
        log(f"① 서버 Glue {a.db}")
        dump("서버", server_datasets(a.db, a.tables, a.sample_rows, ids), out, contract, ref)
    if a.cmd in ("local", "all"):
        paths = a.paths if a.cmd == "local" else a.local
        if paths:
            log("② 로컬 " + ", ".join(paths))
            dump("로컬", local_datasets(paths, a.sample_rows, ids), out, contract, ref)
        elif a.cmd == "local":
            sys.exit("로컬 경로를 주세요")
    if a.cmd in ("compare", "all"):
        log("③ 비교")
        compare(out, a.contract, a.pair)


if __name__ == "__main__":
    main()
