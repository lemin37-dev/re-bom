"""비전 모델(Gemini)로 귀 모양 태그를 고른다 (B 방식: 정해진 보기 + '안 보임' + 근거).

v4: v1(귀·다리·꼬리 질문지)에서 귀 부분만 그대로 떼어 쓴다. 시스템 프롬프트·귀 보기 문구는 v1 과 같다.
귀만 묻는 것의 영향과 견종 표준 용어를 넣은 영향(v2·v3)을 나눠 보려는 시험이다.
- v2: 표준 용어 7개 중에서 고르기 → 3.5-flash 95%→85%, 3.1-flash-lite 89%→86% (2026-11-11)
- v3: v1 기준 + 고른 태그에 속한 표준 용어를 term 으로 → 89%, 86%. 접힌 귀에 붙인 용어(semiprick·button·rose)가
  순종 모양뿐이라 믹스견의 접혀 내려오는 귀가 늘어진 귀로 옮겨 감.
견종 표준 용어(EAR_TERMS)는 판단에 쓰지 않고 태그 설명용 대응표로만 남긴다.
다리·꼬리는 다루지 않는다(2026-11-11 결정).

근거(evidence)는 사진에서 본 것을 한 문장으로 적게 해서 사람이 검수할 때 쓴다. 공고의 품종(kindNm)은 입력에 넣지 않는다
(품종으로 짐작하는 것 방지). 배경 제거는 하지 않는다. 사진 회전(EXIF)만 바로잡는다.

질문지를 바꾸면 PROMPT_VERSION 을 올리고 다시 태깅한다.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass

PROMPT_VERSION = "ear-vision-v4"
NOT_VISIBLE = "안 보임"
UNSURE = "판단 어려움"  # 정답 라벨 전용: 사람이 봐도 판단할 수 없는 사진, 채점에서 뺀다

OPTIONS: dict[str, tuple[str, ...]] = {
    "ear": ("선 귀", "접힌 귀", "늘어진 귀", NOT_VISIBLE),
}
TAG_NAMES = {"ear": "귀"}

# v1 의 귀 판단 기준 (정확도가 가장 높았던 문구, 바꾸지 않는다)
EAR_CRITERIA = {
    "선 귀": "귀가 위로 곧게 서 있음 (끝이 뾰족하든 둥글든)",
    "접힌 귀": "귀 아래쪽은 서 있고 끝부분이 앞이나 옆으로 꺾임, 또는 귀가 뒤로 접혀 붙음",
    "늘어진 귀": "귀 전체가 머리 옆으로 처져 내려옴",
    NOT_VISIBLE: "귀가 털에 묻히거나 사진에서 잘려 판단할 수 없음",
}

# 태그 설명용 견종 표준 귀 용어 (프롬프트에는 넣지 않는다). 출처: https://en.wikipedia.org/wiki/Canine_terminology
EAR_TERMS: dict[str, tuple[str, str]] = {
    "쫑긋 선 귀(prick)": ("선 귀", "곧게 서고 끝이 뾰족함"),
    "박쥐 귀(bat)": ("선 귀", "크고 넓게 서고 끝이 둥긂"),
    "반쯤 선 귀(semiprick)": ("접힌 귀", "서 있고 끝만 앞으로 살짝 꺾임"),
    "버튼 귀(button)": ("접힌 귀", "윗부분이 앞으로 접혀 끝이 눈 쪽을 향함"),
    "장미 귀(rose)": ("접힌 귀", "작은 귀가 뒤로 접혀 안쪽이 살짝 보임"),
    "늘어진 귀(drop)": ("늘어진 귀", "귀 전체가 머리 옆으로 처짐"),
}

# v1 시스템 프롬프트 그대로
SYSTEM_PROMPT = (
    "너는 보호소 유기견 사진에서 몸의 생김새를 기록하는 도우미다. "
    "사진에 직접 보이는 것만 근거로 판단한다. 품종, 털, 다른 부위를 보고 짐작해서 채우지 않는다. "
    "확실히 보이지 않으면 '안 보임'을 고른다."
)

_OPTION_LINES = "\n".join(f"- {tag}: {criterion}" for tag, criterion in EAR_CRITERIA.items())

USER_PROMPT = f"""사진에서 가장 크게, 가운데에 보이는 개 한 마리를 기준으로 귀 모양을 고르세요.
사진 한 장에 여러 컷이 이어 붙어 있으면 모든 컷을 참고하세요.

[귀]
{_OPTION_LINES}

evidence 에 사진에서 실제로 본 것을 한국어 한 문장으로 먼저 쓰고, value 에 보기 하나를 그대로 고르세요."""


def response_schema() -> dict:
    """구조화 출력용 JSON 스키마. 근거를 먼저 쓰게 evidence 를 value 앞에 둔다 (v1 과 같은 모양, 귀만)."""
    return {
        "type": "object",
        "properties": {
            "ear": {
                "type": "object",
                "properties": {
                    "evidence": {"type": "string", "description": "귀에 대해 사진에서 본 것, 한 문장"},
                    "value": {"type": "string", "enum": list(OPTIONS["ear"])},
                },
                "required": ["evidence", "value"],
                "propertyOrdering": ["evidence", "value"],
            }
        },
        "required": ["ear"],
        "propertyOrdering": ["ear"],
    }


@dataclass(frozen=True)
class TagAnswer:
    value: str
    evidence: str
    term: str = ""  # v2·v3 결과에만 있는 견종 표준 용어


def parse_answer(text: str) -> dict[str, TagAnswer]:
    """모델 응답 JSON → 태그별 답. 보기에 없는 값이면 ValueError."""
    data = json.loads(text)
    item = data.get("ear") or {}
    value = (item.get("value") or "").strip()
    if value not in OPTIONS["ear"]:
        raise ValueError(f"ear: 보기에 없는 값 {value!r}")
    return {"ear": TagAnswer(value=value, evidence=(item.get("evidence") or "").strip())}


def terms_for(tag: str) -> list[str]:
    """태그 설명용: 그 태그에 해당하는 견종 표준 용어."""
    return [term for term, (t, _) in EAR_TERMS.items() if t == tag]


def prepare_image(data: bytes) -> tuple[bytes, str]:
    """EXIF 회전만 바로잡는다. 회전 정보가 없으면 원본 그대로. (bytes, mime) 반환."""
    from PIL import Image, ImageOps

    from rebom.embedding.gemini import sniff_mime

    mime = sniff_mime(data)
    with Image.open(io.BytesIO(data)) as im:
        orientation = im.getexif().get(0x0112, 1)
        if orientation in (None, 1):
            return data, mime
        fixed = ImageOps.exif_transpose(im).convert("RGB")
    buf = io.BytesIO()
    fixed.save(buf, format="JPEG", quality=92)
    return buf.getvalue(), "image/jpeg"


@dataclass
class TagStats:
    """태그 하나의 채점 결과. 정답 '안 보임' 사진과 보이는 사진을 나눠 센다."""

    n_visible: int = 0  # 정답이 보이는 사진
    answered: int = 0  # 그중 모델이 보기를 고른 수 ('안 보임'이 아님)
    correct: int = 0  # 그중 맞힌 수
    n_hidden: int = 0  # 정답이 '안 보임'인 사진
    hallucinated: int = 0  # 그중 모델이 보기를 고른 수 (확인할 수 없는 태그)

    def add(self, gold: str, pred: str) -> None:
        if gold == NOT_VISIBLE:
            self.n_hidden += 1
            self.hallucinated += pred != NOT_VISIBLE
            return
        self.n_visible += 1
        if pred != NOT_VISIBLE:
            self.answered += 1
            self.correct += pred == gold

    @staticmethod
    def _ratio(a: int, b: int) -> float | None:
        return a / b if b else None

    @property
    def accuracy(self) -> float | None:
        """보이는 사진 중 맞힌 비율('안 보임'이라 답하면 틀린 것으로 셈)."""
        return self._ratio(self.correct, self.n_visible)

    @property
    def coverage(self) -> float | None:
        return self._ratio(self.answered, self.n_visible)

    @property
    def precision(self) -> float | None:
        """보이는 사진에서 답한 것 중 맞힌 비율."""
        return self._ratio(self.correct, self.answered)

    @property
    def hallucination(self) -> float | None:
        return self._ratio(self.hallucinated, self.n_hidden)

    @property
    def shown_precision(self) -> float | None:
        """화면에 뜰 태그 전체(보이는 사진 + 안 보이는 사진에서 답한 것) 중 맞다고 확인된 비율. 보수적 값."""
        return self._ratio(self.correct, self.answered + self.hallucinated)

