"""PR 상태를 노션 일정 관리 DB 항목에 반영한다.

PR 본문에 노션 일정 관리 항목 링크가 있으면 그 항목을
  - PR 열림·다시 열림·리뷰 요청·본문 수정 → 상태 '진행 중' (대기·시작 전일 때만)
  - PR 머지 → 상태 '완료'
  - 머지 없이 닫힘 → 상태는 그대로
으로 바꾸고, 'GitHub PR' 칸에 PR 링크를 적는다.
본문에 일정 관리 항목 링크가 없으면 구분=작업 항목을 새로 만들고(이름=PR 제목,
일정=PR 생성일~머지일), 그 링크를 PR 본문 끝에 붙인다. 머지 없이 닫힌 PR·봇 PR 은 만들지 않는다.

필요한 환경변수
  NOTION_TOKEN           노션 통합(Internal integration) 토큰
  NOTION_DATA_SOURCE_ID  일정 관리 DB의 data source ID
  GITHUB_EVENT_PATH      pull_request 이벤트 JSON (Actions 기본 제공)
  GITHUB_TOKEN           PR 본문 수정 권한 토큰 (Actions 기본 토큰)
  GITHUB_REPOSITORY      owner/repo (Actions 기본 제공)
  GITHUB_STEP_SUMMARY    실행 요약 파일 (Actions 기본 제공, 없으면 출력만)
  DRY_RUN                1 이면 노션·PR 을 바꾸지 않고 할 일만 출력
  GITHUB_EVENT_NAME      workflow_dispatch 면 기존 PR 전체 백필
  BACKFILL_STATE         백필 대상 PR 상태: all(기본) | open | closed
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
GITHUB_API = "https://api.github.com"

STATUS_PROP = "상태"
KIND_PROP = "구분"
KIND_TASK = "작업"
DATE_PROP = "일정"
LINK_LABEL = "노션 일정"
PR_PROP = "GitHub PR"
IN_PROGRESS = "진행 중"
DONE = "완료"
# 상태는 작업·버그 항목만 바꾼다. 회의·결정·마일스톤은 PR 링크만 적는다
STATUS_KINDS = {None, "작업", "버그"}
# 이 상태일 때만 '진행 중'으로 올린다. 보류·완료는 사람이 정한 것이라 건드리지 않는다.
PROMOTE_FROM = {None, "", "대기", "시작 전"}

# notion.so / notion.site / app.notion.com 링크의 경로 부분만 본다 (?v= 보기 ID 등 쿼리는 제외)
NOTION_URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)?(?:notion\.so|notion\.site|notion\.com)/[^\s)\]>\"'?#]*",
    re.IGNORECASE,
)
PAGE_ID_RE = re.compile(r"([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})(?![0-9a-f])", re.I)


def normalize_id(raw):
    """32자리 hex 를 노션 UUID 형식(8-4-4-4-12)으로"""
    h = raw.replace("-", "").lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def extract_page_ids(text):
    """본문의 노션 링크에서 페이지 ID 를 순서대로, 중복 없이 뽑는다.

    HTML 주석(<!-- -->) 안의 링크는 템플릿 예시일 수 있어 무시한다.
    """
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)
    ids = []
    for url in NOTION_URL_RE.findall(text):
        path = url.split("://", 1)[1]
        matches = PAGE_ID_RE.findall(path)
        if not matches:
            continue
        pid = normalize_id(matches[-1])  # 제목-ID 형식이면 마지막 것이 페이지 ID
        if pid not in ids:
            ids.append(pid)
    return ids


def target_status(action, merged, current):
    """이번 이벤트로 바꿀 상태. 바꿀 게 없으면 None"""
    if action == "closed":
        return DONE if merged and current != DONE else None
    if action in {"opened", "reopened", "ready_for_review", "edited", "synchronize"}:
        return IN_PROGRESS if current in PROMOTE_FROM else None
    return None


def request(method, url, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        print(f"{method} {url} 실패: {e.code} {e.read().decode(errors='replace')}", file=sys.stderr)
        raise


def notion(method, path, body=None):
    headers = {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    return request(method, f"{NOTION_API}{path}", headers, body)


def github(method, path, body=None):
    headers = {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    return request(method, f"{GITHUB_API}{path}", headers, body)


def kst_date(iso):
    """GitHub UTC 시각(2026-11-24T06:17:06Z) → 한국 날짜(YYYY-MM-DD)"""
    from datetime import datetime, timedelta, timezone

    if not iso:
        return None
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return t.astimezone(timezone(timedelta(hours=9))).date().isoformat()


def should_create(pr, action, merged):
    """링크 없는 PR 에 새 작업 항목을 만들지"""
    if (pr.get("user") or {}).get("type") == "Bot":
        return False
    if action == "closed":
        return merged
    return action in {"opened", "reopened", "ready_for_review", "edited"}


def new_task_body(pr, data_source_id, action, merged):
    """일정 관리 DB 에 만들 구분=작업 페이지"""
    start = kst_date(pr.get("created_at"))
    end = kst_date(pr.get("merged_at")) if merged else None
    props = {
        "이름": {"title": [{"text": {"content": pr.get("title") or f"PR #{pr['number']}"}}]},
        KIND_PROP: {"select": {"name": KIND_TASK}},
        STATUS_PROP: {"status": {"name": target_status(action, merged, None) or IN_PROGRESS}},
        PR_PROP: {"url": pr["html_url"]},
    }
    if start:
        props[DATE_PROP] = {"date": {"start": start, "end": end if end and end != start else None}}
    return {"parent": {"type": "data_source_id", "data_source_id": data_source_id}, "properties": props}


def body_with_link(body, page_url):
    """PR 본문 끝에 노션 일정 링크 한 줄을 붙인다"""
    return f"{(body or '').rstrip()}\n\n{LINK_LABEL}: {page_url}\n"


def in_data_source(page, data_source_id):
    parent = page.get("parent") or {}
    pid = parent.get("data_source_id") or parent.get("database_id") or ""
    return pid.replace("-", "") == data_source_id.replace("-", "")


def page_title(page):
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop["title"]).strip() or "(제목 없음)"
    return "(제목 없음)"


def current_status(page):
    status = (page.get("properties", {}).get(STATUS_PROP) or {}).get("status")
    return status.get("name") if status else None


def page_kind(page):
    select = (page.get("properties", {}).get(KIND_PROP) or {}).get("select")
    return select.get("name") if select else None


def build_update(page, pr_url, action, merged):
    """노션 PATCH 본문. 바꿀 게 없으면 None"""
    props = {}
    new_status = target_status(action, merged, current_status(page)) if page_kind(page) in STATUS_KINDS else None
    if new_status:
        props[STATUS_PROP] = {"status": {"name": new_status}}
    existing = (page.get("properties", {}).get(PR_PROP) or {}).get("url")
    if existing != pr_url:
        props[PR_PROP] = {"url": pr_url}
    return {"properties": props} if props else None


def summarize(lines):
    print("\n".join(lines))
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def sync_pr(pr, action, merged, data_source_id, dry_run):
    """PR 하나를 노션에 반영하고 요약 줄을 돌려준다"""
    lines = [f"### #{pr['number']} {pr.get('title') or ''} ({action}{', merged' if merged else ''})"]
    ids = extract_page_ids(pr.get("body"))
    linked = 0
    for pid in ids:
        try:
            page = notion("GET", f"/pages/{pid}")
        except urllib.error.HTTPError as e:
            lines.append(f"- `{pid}`: 페이지를 읽지 못함 ({e.code}). 노션 통합이 연결돼 있는지 확인")
            continue
        if not in_data_source(page, data_source_id):
            continue  # 일정 관리 DB 밖의 노션 링크(회의록·설계 문서 등)는 조용히 무시
        linked += 1
        title = page_title(page)
        update = build_update(page, pr["html_url"], action, merged)
        if not update:
            lines.append(f"- {title}: 바꿀 것 없음 (상태 {current_status(page) or '없음'})")
            continue
        changes = ", ".join(
            f"{k} → {v.get('status', {}).get('name') or v.get('url')}" for k, v in update["properties"].items()
        )
        if not dry_run:
            notion("PATCH", f"/pages/{pid}", update)
        lines.append(f"- {'[DRY RUN] ' if dry_run else ''}{title}: {changes}")

    if not linked:
        if not should_create(pr, action, merged):
            lines.append("- 일정 관리 항목 링크가 없지만 이번 이벤트에는 새 항목을 만들지 않음")
        elif dry_run:
            lines.append(f"- [DRY RUN] 새 작업 항목 생성 예정 (상태 {target_status(action, merged, None)})")
        else:
            repo = os.environ["GITHUB_REPOSITORY"]
            # 이벤트 JSON 의 본문은 이벤트 시점 것이다. 앞선 실행이 링크를 붙였을 수 있어 최신 본문을 다시 본다
            fresh = github("GET", f"/repos/{repo}/pulls/{pr['number']}").get("body")
            same = find_by_pr_url(data_source_id, pr["html_url"])
            if set(extract_page_ids(fresh)) - set(ids):
                lines.append("- 최신 PR 본문에 새 노션 링크가 있어 항목을 만들지 않음 (이미 연결됨)")
            elif same:
                # 'GitHub PR' 칸에 이 PR 이 이미 적힌 항목이 있으면 그 항목을 쓴다 (백필 다시 실행 등)
                update = build_update(same, pr["html_url"], action, merged)
                if update:
                    notion("PATCH", f"/pages/{same['id']}", update)
                lines.append(f"- 'GitHub PR' 칸이 같은 항목에 연결: {page_title(same)}")
            else:
                created = notion("POST", "/pages", new_task_body(pr, data_source_id, action, merged))
                github("PATCH", f"/repos/{repo}/pulls/{pr['number']}", {"body": body_with_link(fresh, created["url"])})
                lines.append(f"- 새 작업 항목 생성: {created['url']} (PR 본문에 링크 추가)")
    return lines


def find_by_pr_url(data_source_id, pr_url):
    """'GitHub PR' 칸이 이 PR 인 일정 항목 (없으면 None)"""
    res = notion("POST", f"/data_sources/{data_source_id}/query", {
        "filter": {"property": PR_PROP, "url": {"equals": pr_url}},
        "page_size": 1,
    })
    results = (res or {}).get("results") or []
    return results[0] if results else None


def list_prs(repo, state):
    """저장소 PR 전체 (오래된 것부터). state: all | open | closed"""
    prs, page = [], 1
    while True:
        batch = github("GET", f"/repos/{repo}/pulls?state={state}&sort=created&direction=asc&per_page=100&page={page}")
        prs += batch
        if len(batch) < 100:
            return prs
        page += 1


def backfill_event(pr):
    """기존 PR 을 지금 상태에 맞는 이벤트로 바꾼다: 머지됨→closed+merged, 닫힘→closed, 열림→opened"""
    merged = bool(pr.get("merged_at"))
    return ("closed" if pr.get("state") == "closed" else "opened"), merged


def backfill(data_source_id, dry_run):
    """수동 실행: 기존 PR 전체를 같은 규칙으로 한 번 반영"""
    import time

    repo = os.environ["GITHUB_REPOSITORY"]
    state = os.environ.get("BACKFILL_STATE") or "all"
    prs = list_prs(repo, state)
    summarize([f"## 노션 일정 백필 ({state}, PR {len(prs)}개{', DRY RUN' if dry_run else ''})"])
    for pr in prs:
        action, merged = backfill_event(pr)
        summarize(sync_pr(pr, action, merged, data_source_id, dry_run))
        if not dry_run:
            time.sleep(0.4)  # 노션 API 초당 3회 제한


def main():
    data_source_id = os.environ["NOTION_DATA_SOURCE_ID"]
    dry_run = os.environ.get("DRY_RUN") == "1"
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        backfill(data_source_id, dry_run)
        return
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        event = json.load(f)
    pr = event["pull_request"]
    merged = bool(pr.get("merged") or pr.get("merged_at"))
    summarize(sync_pr(pr, event.get("action", ""), merged, data_source_id, dry_run))


if __name__ == "__main__":
    main()
