"""글(특이사항) 태그: 성격 8개와 건강정보 2값. 질문지·태그 코드·판정 규칙. (docs/data_contract.md 15절)

질문 문구는 data/text_questions_v1.json 에 있다. jev_final_test 에서 시험한 질문을 글자 그대로 옮긴 것이다
(성격 personality-v10 = 시험 p12, 건강정보 health-info-s4c, 2026-11-23 채택). 이 레포에서는 이것을 v1 로 부른다.
문구를 고치면 JSON 을 새 파일(v2)로 만들고 PROMPT_VERSION 을 올린다. 버전이 바뀌면 모든 공고를 다시 태깅한다.

판정 (기준표 1-0·2-0, 2026-11-23):
- 성격: 질문 8개(예/아니오, noul) 각각의 예 확률이 0.75 이상이면 태그. 0.4~0.75 는 태그로 쓰지 않고 detail.review 에 남긴다.
  한 글에 여러 태그가 붙을 수 있다(순함+사나움처럼 뜻이 반대여도 글에 둘 다 있으면 둘 다 붙인다).
- 건강정보: 질문 1개(choice). 확률이 높은 값을 붙인다. 정보 있음 = health.info, 정보 없음 = health.none.
  두 값 모두 라벨로 검색·화면에 넘긴다(2026-11-23 결정). 정보 없음은 "건강하다"는 뜻이 아니라 글에 건강 내용이 없다는 뜻이다.
  1등 확률이 0.4 미만이면 detail.review = true.
- 확률 원값은 모든 태그 행의 detail.probs 와 요청 기록 raw_answer 에 남긴다(Gold 규칙: 원 확률 보존).
- 판정 근거 글: Jev 는 근거 구절을 돌려주지 않으므로, Jev 에 실제로 보낸 글(전화번호 [번호] 가림)을 detail.input_text 에 남긴다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from rebom.tagging.tag_codes import TagDef

QUESTIONS_FILE = "text_questions_v1.json"
YES_HI, YES_LO = 0.75, 0.4
CHOICE_HI = 0.4
TASK_PERSONALITY = "text_personality"
TASK_HEALTH = "text_health"
HEALTH_INFO, HEALTH_NONE = "정보 있음", "정보 없음"

PERSONALITY_TAGS: dict[str, TagDef] = {
    # 질문 이름 → 태그. 판정 기준 문장은 기준표 1-3-4(2026-11-23)와 같은 뜻의 요약이다.
    "trait_soon": TagDef("personality.gentle", "순함", "순하다·온순·얌전·차분·점잖·착하다·다정. 예 확률 0.75 이상"),
    "trait_friendly": TagDef("personality.friendly", "친화적",
                             "사람을 좋아함·잘 따름·애교·손길 수용·만지기 가능·사회성·경계심 없음. 예 확률 0.75 이상"),
    "trait_fear": TagDef("personality.timid", "겁많음", "겁·소심·수줍·내성적·불안·손길 피함. 예 확률 0.75 이상"),
    "trait_wary": TagDef("personality.wary", "경계",
                         "경계심·낯가림·사람(손)을 피함·먼저 다가가지 않음·방어적 입질. 예 확률 0.75 이상"),
    "trait_active": TagDef("personality.active", "활발", "활발·활동적·활달·에너지 넘침·명랑·발랄. 예 확률 0.75 이상"),
    "trait_aggr": TagDef("personality.aggressive", "사나움",
                         "사나움·공격성·하악질·으르렁·입질(방어적이라고 적힌 입질 제외). 예 확률 0.75 이상"),
    "trait_sensitive": TagDef("personality.sensitive", "예민", "예민·민감·까칠·도도·새침·성깔. 예 확률 0.75 이상"),
    "trait_curious": TagDef("personality.curious", "호기심", "호기심·장난기·개구쟁이·공놀이 좋아함. 예 확률 0.75 이상"),
}
HEALTH_TAGS: dict[str, TagDef] = {
    HEALTH_INFO: TagDef("health.info", "건강정보 있음",
                        "글에 지금 건강 상태를 알 수 있는 내용(건강 표현 또는 건강상 이상)이 있음. 확률이 더 높은 쪽"),
    HEALTH_NONE: TagDef("health.none", "건강정보 없음", "건강 상태를 알 수 있는 내용이 없음(건강하다는 뜻이 아님)"),
}


def _load() -> dict:
    with resources.files("rebom.tagging").joinpath("data", QUESTIONS_FILE).open(encoding="utf-8") as f:
        return json.load(f)


_Q = _load()
PERSONALITY_VERSION = _Q["personality"]["prompt_version"]  # text-personality-v1
HEALTH_VERSION = _Q["health"]["prompt_version"]  # text-health-v1
HEALTH_QUESTION = _Q["health"]["question_name"]  # health_info
VERSIONS = {TASK_PERSONALITY: PERSONALITY_VERSION, TASK_HEALTH: HEALTH_VERSION}
# 한 요청에 두 작업의 질문 9개를 함께 보낸다(시험 2026-11-23: 따로 보낼 때보다 요청 수 절반, 비용 약 7% 적음)
QUESTIONS: dict = {**_Q["personality"]["questions"], **_Q["health"]["questions"]}
REQUEST_TASK = "text_jev"
REQUEST_VERSION = f"{PERSONALITY_VERSION}+{HEALTH_VERSION}"

assert set(_Q["personality"]["questions"]) == set(PERSONALITY_TAGS), "질문 이름과 성격 태그 표가 다릅니다"
assert set(_Q["health"]["labels"]) == set(HEALTH_TAGS), "건강정보 보기와 태그 표가 다릅니다"


@dataclass(frozen=True)
class Decision:
    """한 글의 판정. tags 는 (TagDef, 확률) 목록, 확률 높은 순."""

    personality: list[tuple[TagDef, float]]
    personality_probs: dict[str, float]  # 태그 이름 → 예 확률
    review: list[str]  # 0.4~0.75 인 태그 이름
    health: TagDef
    health_prob: float
    health_probs: dict[str, float]
    health_review: bool


def decide(answers: dict) -> Decision:
    """Jev answers → 판정. 질문이 빠졌거나 형식이 다르면 KeyError·ValueError·TypeError."""
    from rebom.tagging.jev_client import yes_prob

    probs = {PERSONALITY_TAGS[q].label: round(yes_prob(answers[q]), 4) for q in PERSONALITY_TAGS}
    by_label = {t.label: t for t in PERSONALITY_TAGS.values()}
    tags = sorted(((by_label[k], p) for k, p in probs.items() if p >= YES_HI), key=lambda x: -x[1])
    review = [k for k, p in sorted(probs.items(), key=lambda x: -x[1]) if YES_LO < p < YES_HI]
    hp = {str(k): round(float(v), 4) for k, v in (answers[HEALTH_QUESTION].get("probabilities") or {}).items()}
    if not hp or not set(hp) <= set(HEALTH_TAGS):
        raise ValueError(f"건강정보 보기가 다릅니다: {sorted(hp)}")
    top = max(hp, key=hp.get)
    return Decision(tags, probs, review, HEALTH_TAGS[top], hp[top], hp, hp[top] < CHOICE_HI)


def personality_detail(d: Decision, request_id: str | None, input_text: str | None = None) -> str:
    return json.dumps({"probs": d.personality_probs, "review": d.review, "request_id": request_id, "input_text": input_text},
                      ensure_ascii=False)


def health_detail(d: Decision, request_id: str | None, input_text: str | None = None) -> str:
    return json.dumps({"probs": d.health_probs, "review": d.health_review, "request_id": request_id, "input_text": input_text},
                      ensure_ascii=False)
