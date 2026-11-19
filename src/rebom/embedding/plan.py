"""manifest 의 현재 사진과 S3 에 이미 있는 결과를 비교해 이번에 Gemini 로 보낼 사진을 정한다.

보내기 전 규칙: 칸별 최신 버전만, 같은 sha256 은 1번만, 이미 만든 sha256 벡터는 재사용, 빈 사진으로
판정된 sha256 은 다시 보내지 않음. 빈 사진 판정 자체는 사진을 받아야 알 수 있어 실행 중에 한다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from rebom.embedding import manifest as mf
from rebom.embedding.staging import Staged


@dataclass
class Plan:
    current: dict[tuple[str, int], mf.Photo]
    send: dict[str, list[mf.Photo]] = field(default_factory=dict)  # sha256 → 그 파일을 쓰는 칸들 (보낼 것)
    reuse: list[mf.Photo] = field(default_factory=list)  # 이미 만든 벡터를 복사할 칸
    skip_known: list[mf.Photo] = field(default_factory=list)  # 전에 빈 사진 등으로 건너뛴 sha256
    stats: dict[str, Any] = field(default_factory=dict)

    def send_order(self) -> list[str]:
        """대표 칸(공고·칸 순 첫 번째) 기준 정렬. 재실행해도 같은 순서."""
        return sorted(self.send, key=lambda sha: self.send[sha][0].key)


def build(manifests: list[dict], staged: Staged) -> Plan:
    runs = sorted(manifests, key=lambda d: (d.get("generated_at") or "", d.get("run_id") or ""))
    entries = [(m.get("run_id"), p) for m in runs for p in mf.parse_manifest(m)]
    versions = {(p.key, p.version) for _, p in entries}
    current = mf.latest_photos(runs)

    seen: set[tuple[str, int]] = set()
    new_keys_by_run: dict[str, int] = {}
    for run_id, p in entries:
        if p.key not in seen:
            seen.add(p.key)
            new_keys_by_run[run_id] = new_keys_by_run.get(run_id, 0) + 1

    plan = Plan(current=current)
    done = 0
    for p in sorted(current.values(), key=lambda p: p.key):
        row = staged.rows.get(p.key)
        if row is not None and row["sha256"] == p.sha256:
            done += 1
        elif p.sha256 in staged.vectors:
            plan.reuse.append(p)
        elif p.sha256 in staged.skipped:
            plan.skip_known.append(p)
        else:
            plan.send.setdefault(p.sha256, []).append(p)

    sha_count = Counter(p.sha256 for p in current.values())
    plan.stats = {
        "manifest_file_entries": len(entries),  # 여러 manifest 에 걸친 files 항목 합
        "repeated_in_manifests": len(entries) - len(versions),  # 같은 칸·같은 버전이 다시 나온 수
        "old_versions_dropped": len(versions) - len(current),  # _v00N 교체로 밀려난 옛 버전
        "current_photos": len(current),  # 칸 = (공고, 슬롯) 키 수
        "unique_sha256": len(sha_count),
        "duplicate_photos": len(current) - len(sha_count),  # 같은 파일을 다른 칸·공고가 쓰는 수
        "new_keys_by_run": new_keys_by_run,
        "already_staged": done,
        "reuse_vector": len(plan.reuse),
        "skip_known_blank": len(plan.skip_known),
        "to_send_sha256": len(plan.send),
        "to_send_photos": sum(len(v) for v in plan.send.values()),
    }
    return plan


@dataclass
class LoadPlan:
    """S3 결과 → RDS 변경분. RDS 는 서비스 중인 모델의 현재 사진 벡터만 두는 사본이다."""

    write: list[tuple[mf.Photo, list[float]]] = field(default_factory=list)  # 새 칸 + sha256 이 바뀐 칸
    stats: dict[str, Any] = field(default_factory=dict)


def build_load(staged: Staged, current: dict[tuple[str, int], mf.Photo], in_rds: dict[tuple[str, int], str],
               full: bool = False) -> LoadPlan:
    """in_rds: RDS 의 (공고, 칸) → sha256. full 이면 같아도 다시 쓴다(전처리를 바꿨을 때 등)."""
    lp = LoadPlan()
    new = changed = same = not_staged = 0
    for key, p in sorted(current.items()):
        row = staged.rows.get(key)
        if row is None or row["sha256"] != p.sha256:  # 아직 임베딩 안 됨, 또는 그사이 사진이 바뀜
            not_staged += 1
            continue
        old = in_rds.get(key)
        if old == p.sha256 and not full:
            same += 1
            continue
        new += old is None
        changed += old is not None and old != p.sha256
        lp.write.append((p, row["embedding"]))
    # RDS 에 있지만 manifest 의 현재 사진이 아닌 칸 (교체 전 sha256 이면서 새 벡터가 아직 없거나, 칸이 사라짐).
    # 지우지 않는다(11/19 확정): 종료 공고는 보호중으로 복귀할 수 있어 검색이 공고 상태(기본 보호중)로 거른다.
    written = {p.key for p, _ in lp.write}
    stale = sum(1 for k, sha in in_rds.items()
                if k not in current or (current[k].sha256 != sha and k not in written))
    lp.stats = {
        "current_photos": len(current), "in_rds": len(in_rds), "unchanged_skip": same,
        "new": new, "changed_sha256": changed, "rewrite_same": len(lp.write) - new - changed,
        "to_write": len(lp.write), "not_staged_yet": not_staged, "rds_not_current": stale,
    }
    return lp
