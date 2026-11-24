"""글 카드 템플릿: card_templates/<버전>.json 을 읽고, 검사하고, 카드 글을 만든다.

- 템플릿 파일은 문구·순서·포함 항목만 정한다. 값(체중 계산, 털색 분류, 전화번호 가리기)은 VALUES 의 파이썬 함수가 만든다.
  개인정보 가리기는 값 함수 안에 있어 템플릿에서 끌 수 없다.
- 한번 합쳐진 템플릿 파일은 고치지 않는다. 내용이 바뀌면 lock.json 의 지문과 달라 읽을 때 멈춘다.
  문구를 바꾸려면 새 버전 파일(A3.json)을 더하고 lock.json 에 지문을 적는다.
- 어떤 템플릿 내용으로 벡터를 만들었는지는 Gold 버전 레지스트리에 내용과 지문을 함께 남긴다 (text_version.py).

지문 보기: python -m rebom.embedding.card_template
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

TEMPLATE_DIR = Path(__file__).resolve().parent / "card_templates"
NOT_TEMPLATES = ("lock", "golden")  # golden.json 과 golden_<버전>.json 은 예시 파일
NOTE_SOURCES = ("special_mark", "info_text")  # info_text 는 비어 있으면 special_mark 를 쓴다
PARAMS: dict[str, type] = {"size_small_max_inclusive": bool, "note_source": str, "allow_soci": bool}
TOP_KEYS = {"template_version", "description", "fields", "separator", "tail", "params"}


class TemplateError(ValueError):
    """템플릿 파일이 잘못됐거나 등록된 내용과 다르다. 모델을 부르기 전에 멈춘다."""


@dataclass(frozen=True)
class Context:
    """카드 1장을 만들 때 값 함수가 보는 것. row 는 공공데이터 API 이름 dict 다."""

    row: Mapping[str, Any]
    params: Mapping[str, Any]
    tags: tuple[tuple[str, Any], ...] = ()
    soci: bool = False


# ---- 값 함수: 없으면 None (그 항목은 카드에서 빠진다) ----------------------------------------------------------------

def _breed(c: Context):
    kind = c.row.get("kindNm")
    return kind if isinstance(kind, str) and kind else None


def _birth_year(c: Context):
    from rebom.embedding import text_card

    return text_card.birth_year(c.row.get("age"))


def _code(column: str) -> Callable[[Context], Any]:
    def value(c: Context):
        from rebom.embedding import text_card

        return text_card._s(c.row.get(column)) or None
    return value


def _weight_size(c: Context):
    from rebom.embedding import text_card

    k = text_card.kg(c.row.get("weight"))
    return None if k is None else f"{k:g}kg {text_card.size_label(k, c.params['size_small_max_inclusive'])}"


def _color_families(c: Context):
    from rebom.embedding import text_card

    return "·".join(text_card.color_tag(c.row.get("colorCd"))) or None


def _color_raw(c: Context):
    color = c.row.get("colorCd")
    return color if isinstance(color, str) else None  # 빈 글자도 그대로 (벤치마크와 같음)


def _tags(c: Context):
    from rebom.embedding import text_card

    values = ((label, text_card._s(value).strip()) for label, value in c.tags)
    return [f"{label} {value}" for label, value in values if value.lower() not in text_card.UNKNOWN_TAG]


def _note(c: Context):
    from rebom.embedding import text_card

    return text_card.persona(c.row, c.soci) or None  # 전화번호·관리번호는 여기서 가린다


VALUES: dict[str, Callable[[Context], Any]] = {
    "breed": _breed, "birth_year": _birth_year, "sex": _code("sexCd"), "neuter": _code("neuterYn"),
    "weight_size": _weight_size, "color_families": _color_families, "color_raw": _color_raw,
    "tags": _tags, "note": _note,
}
LIST_VALUES = {"tags"}  # 여러 조각을 내는 값. text·map 을 쓸 수 없다


# ---- 읽기·검사 -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Template:
    version: str
    content: Mapping[str, Any]

    @property
    def sha256(self) -> str:
        return content_sha256(self.content)

    @property
    def params(self) -> Mapping[str, Any]:
        return self.content["params"]

    @property
    def uses_tags(self) -> bool:
        return any(f["value"] == "tags" for f in self.content["fields"])

    @property
    def note_from_info_text(self) -> bool:
        return self.params["note_source"] == "info_text"


def content_sha256(content: Any) -> str:
    """내용의 지문. 줄바꿈·들여쓰기·키 순서는 보지 않는다 (목록 순서는 본다)."""
    text = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _field_problems(name: str, field: Any) -> list[str]:
    if not isinstance(field, dict) or field.get("value") not in VALUES:
        return [f"{name}: 없는 값 {field.get('value') if isinstance(field, dict) else field!r} (쓸 수 있는 값 {sorted(VALUES)})"]
    problems = [f"{name}: 모르는 키 {k}" for k in sorted(set(field) - {"value", "text", "map"})]
    if field["value"] in LIST_VALUES and len(field) > 1:
        problems.append(f"{name}: {field['value']} 에는 text·map 을 쓸 수 없음")
    if "text" in field and (not isinstance(field["text"], str) or field["text"].count("{}") != 1):
        problems.append(f"{name}: text 에는 {{}} 가 하나 있어야 함")
    mapping = field.get("map", {"-": "-"})
    if not isinstance(mapping, dict) or not mapping or any(not isinstance(v, str) or not v for v in mapping.values()):
        problems.append(f"{name}: map 은 비어 있지 않은 글자 표여야 함")
    return problems


def validate(content: Any, version: str) -> None:
    """잘못된 템플릿이면 이유를 모두 모아 멈춘다."""
    if not isinstance(content, dict):
        raise TemplateError(f"{version}: 템플릿은 JSON 객체여야 함")
    problems = [f"모르는 키 {k}" for k in sorted(set(content) - TOP_KEYS)]
    problems += [f"빠진 키 {k}" for k in sorted(TOP_KEYS - {"description"} - set(content))]
    if problems:
        raise TemplateError(f"{version}: " + "; ".join(problems))
    if content["template_version"] != version:
        problems.append(f"파일 이름({version})과 template_version({content['template_version']})이 다름")
    fields = content["fields"]
    if not isinstance(fields, list) or not fields:
        problems.append("fields 는 비어 있지 않은 목록이어야 함")
    else:
        for i, field in enumerate(fields):
            problems += _field_problems(f"fields[{i}]", field)
        names = [f.get("value") for f in fields if isinstance(f, dict)]
        problems += [f"겹치는 값 {n}" for n in sorted({str(n) for n in names if names.count(n) > 1})]
    problems += _field_problems("tail", content["tail"])
    if isinstance(content["tail"], dict) and content["tail"].get("value") in LIST_VALUES:
        problems.append("tail 에는 여러 조각을 내는 값을 쓸 수 없음")
    if not isinstance(content["separator"], str) or not content["separator"]:
        problems.append("separator 는 비어 있지 않은 글자여야 함")
    params = content["params"]
    if not isinstance(params, dict) or set(params) != set(PARAMS):
        problems.append(f"params 는 {sorted(PARAMS)} 를 모두 가져야 함")
    else:
        problems += [f"params.{k} 는 {t.__name__} 여야 함" for k, t in PARAMS.items() if type(params[k]) is not t]
        if params["note_source"] not in NOTE_SOURCES:
            problems.append(f"params.note_source 는 {NOTE_SOURCES} 중 하나")
    if problems:
        raise TemplateError(f"{version}: " + "; ".join(problems))


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def available(directory: Path = TEMPLATE_DIR) -> tuple[str, ...]:
    return tuple(sorted(p.stem for p in directory.glob("*.json") if not p.stem.startswith(NOT_TEMPLATES)))


def load_from(directory: Path, version: str) -> Template:
    """템플릿을 읽고 검사한다. lock.json 의 지문과 다르면 멈춘다."""
    if version not in available(directory):
        raise TemplateError(f"Unsupported text card template: {version} (있는 템플릿 {available(directory)})")
    content = _read(directory / f"{version}.json")
    validate(content, version)
    template = Template(version, content)
    locked = _read(directory / "lock.json").get(version)
    if locked is None:
        raise TemplateError(f"{version}: lock.json 에 지문이 없음. 새 템플릿이면 \"{version}\": \"{template.sha256}\" 를 더해 주세요.")
    if locked != template.sha256:
        raise TemplateError(f"{version}: 템플릿 내용이 등록된 지문과 다름 (지금 {template.sha256[:12]}, 등록 {locked[:12]}). "
                            "기존 템플릿은 고치지 않고 새 버전 파일로 추가해 주세요.")
    return template


@cache
def load(version: str) -> Template:
    return load_from(TEMPLATE_DIR, version)


def golden(version: str) -> dict[str, Any]:
    """지문용 고정 예시. 새 값이 필요한 템플릿은 golden_<버전>.json 을 따로 둔다 (기존 예시는 고치지 않는다)."""
    own = TEMPLATE_DIR / f"golden_{version}.json"
    return _read(own if own.exists() else TEMPLATE_DIR / "golden.json")


# ---- 카드 만들기 -----------------------------------------------------------------------------------------------------

def _pieces(field: Mapping[str, Any], context: Context) -> list[str]:
    value = VALUES[field["value"]](context)
    if value is None:
        return []
    if field["value"] in LIST_VALUES:
        return list(value)
    if "map" in field:
        value = field["map"].get(value)
        if value is None:
            return []
    return [field.get("text", "{}").replace("{}", str(value))]


def render(template: Template, row: Mapping[str, Any], tags: Iterable[tuple[str, Any]] | None = None,
           soci: bool = False) -> str:
    """row: API 이름 dict · tags: [(이름표, 값), …] · soci: 사회성 포함 (allow_soci 인 템플릿만)"""
    if soci and not template.params["allow_soci"]:
        raise ValueError(f"{template.version} 에는 사회성을 넣지 않음")
    context = Context(row, template.params, tuple(tags or ()), soci)
    body = template.content["separator"].join(p for f in template.content["fields"] for p in _pieces(f, context) if p)
    return body + "".join(_pieces(template.content["tail"], context))


if __name__ == "__main__":
    for name in available():
        raw = _read(TEMPLATE_DIR / f"{name}.json")
        validate(raw, name)
        print(f'"{name}": "{content_sha256(raw)}"')
