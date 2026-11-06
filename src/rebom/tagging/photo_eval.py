"""사진 태그 채점.

'확률이 기준을 넘은 태그만 저장'한다고 보고, 기준 확률마다 채택률과 채택된 태그의 정확도를 잰다.
'알 수 없음'으로 답하거나 기준 미달이면 오답이 아니라 미채택으로 센다.
정답이 '알 수 없음'이거나 빈 칸인 건은 채점에서 뺀다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .photo_tags import UNKNOWN

THRESHOLDS = tuple(round(0.5 + 0.05 * i, 2) for i in range(9))  # 0.5 ~ 0.9
CAL_BINS = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0001)


def top(probs: dict[str, float]) -> tuple[str, float]:
    label, p = max(probs.items(), key=lambda kv: kv[1])
    return label, p


def decide(probs: dict[str, float] | None, threshold: float) -> str | None:
    """채택된 보기. 미채택이면 None."""
    if not probs:
        return None
    label, p = top(probs)
    if label == UNKNOWN or p < threshold:
        return None
    return label


@dataclass
class ThresholdRow:
    threshold: float
    n: int
    answered: int
    correct: int

    @property
    def coverage(self) -> float:
        return self.answered / self.n if self.n else 0.0

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.answered if self.answered else None


def scoreable(gold: str | None) -> bool:
    return bool(gold) and gold != UNKNOWN


def threshold_table(
    pairs: list[tuple[str | None, dict[str, float] | None]], thresholds=THRESHOLDS
) -> list[ThresholdRow]:
    """pairs = [(정답, 예측 확률)]. 예측이 없으면(None) 미채택."""
    rows = []
    usable = [(g, p) for g, p in pairs if scoreable(g)]
    for t in thresholds:
        answered = correct = 0
        for gold, probs in usable:
            pred = decide(probs, t)
            if pred is not None:
                answered += 1
                correct += pred == gold
        rows.append(ThresholdRow(t, len(usable), answered, correct))
    return rows


def macro_f1(pairs: list[tuple[str | None, dict[str, float] | None]], threshold: float) -> float:
    """보기별 F1 평균. 미채택은 어느 보기도 맞히지 못한 것으로 센다(재현율에만 손해)."""
    usable = [(g, decide(p, threshold)) for g, p in pairs if scoreable(g)]
    labels = sorted({g for g, _ in usable})
    if not labels:
        return 0.0
    f1s = []
    for c in labels:
        tp = sum(1 for g, p in usable if g == c and p == c)
        fp = sum(1 for g, p in usable if g != c and p == c)
        fn = sum(1 for g, p in usable if g == c and p != c)
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return sum(f1s) / len(f1s)


def calibration(pairs: list[tuple[str | None, dict[str, float] | None]], bins=CAL_BINS) -> list[tuple[str, int, float | None]]:
    """최고 확률 구간별 (구간, 건수, 실제 정답률). '알 수 없음' 답은 뺀다."""
    out = []
    usable = []
    for g, p in pairs:
        if scoreable(g) and p:
            label, prob = top(p)
            if label != UNKNOWN:
                usable.append((prob, label == g))
    for lo, hi in zip(bins, bins[1:]):
        hits = [ok for prob, ok in usable if lo <= prob < hi]
        name = f"{lo:.1f}~{min(hi, 1.0):.1f}"
        out.append((name, len(hits), sum(hits) / len(hits) if hits else None))
    return out


def confusion(pairs: list[tuple[str | None, dict[str, float] | None]], threshold: float) -> Counter:
    """(정답, 예측) 건수. 미채택은 예측을 '미채택'으로 적는다."""
    return Counter((g, decide(p, threshold) or "미채택") for g, p in pairs if scoreable(g))


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    """두 라벨러의 일치도. 둘 다 값이 있는 건만 넣는다."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


# --- 털색 비율 ---

def ratio_error(gold: dict[str, float], pred: dict[str, float]) -> float:
    """두 비율의 차이(%p). 색별 차이 절댓값 합의 절반 = 다른 색으로 잘못 칠해진 면적 비율."""
    keys = set(gold) | set(pred)
    return 50 * sum(abs(gold.get(k, 0) - pred.get(k, 0)) for k in keys)


def main_color(ratios: dict[str, float]) -> str | None:
    return max(ratios, key=ratios.get) if ratios else None


@dataclass
class RatioSummary:
    n: int
    main_match: float | None
    mean_error_pp: float | None


def ratio_summary(pairs: list[tuple[dict[str, float], dict[str, float]]]) -> RatioSummary:
    usable = [(g, p) for g, p in pairs if g and p]
    if not usable:
        return RatioSummary(0, None, None)
    match = sum(main_color(g) == main_color(p) for g, p in usable) / len(usable)
    err = sum(ratio_error(g, p) for g, p in usable) / len(usable)
    return RatioSummary(len(usable), match, err)


# --- 닮은 품종 ---

def topk_hit(gold: str, probs: dict[str, float], k: int) -> bool:
    ranked = [b for b, _ in sorted(probs.items(), key=lambda kv: -kv[1]) if b != UNKNOWN]
    return gold in ranked[:k]


def breed_summary(pairs: list[tuple[str | None, dict[str, float] | None]]) -> dict[str, float | int | None]:
    """품종이 적힌 공고만 채점. 정답이 후보 목록에 없으면 따로 센다(후보 목록 보강용)."""
    usable = [(g, p) for g, p in pairs if g and p]
    if not usable:
        return {"n": 0, "top1": None, "top3": None, "gold_not_in_candidates": 0}
    not_in = sum(1 for g, p in usable if g not in p)
    return {
        "n": len(usable),
        "top1": sum(topk_hit(g, p, 1) for g, p in usable) / len(usable),
        "top3": sum(topk_hit(g, p, 3) for g, p in usable) / len(usable),
        "gold_not_in_candidates": not_in,
    }
