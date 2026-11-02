"""노션 일정 관리 DB의 '결정' 항목을 GitHub 이슈로 등록한다.

구분=결정 이고 'GitHub 이슈' 칸이 비어 있는 페이지마다 이슈를 하나 만들고,
만든 이슈 링크를 다시 노션 'GitHub 이슈' 칸에 적는다. (중복 등록 방지)

필요한 환경변수
  NOTION_TOKEN           노션 통합(Internal integration) 토큰
  NOTION_DATA_SOURCE_ID  일정 관리 DB의 data source ID
  GITHUB_TOKEN           이슈 쓰기 권한 토큰 (Actions 기본 토큰)
  GITHUB_REPOSITORY      owner/repo (Actions 기본 제공)
  DRY_RUN                1 이면 이슈를 만들지 않고 목록만 출력
"""

import json
import os
import sys
import urllib.error
import urllib.request

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
GITHUB_API = "https://api.github.com"

KIND_PROP = "구분"
KIND_VALUE = "결정"
ISSUE_PROP = "GitHub 이슈"
LABEL = "decision"
MARKER = "notion-page-id"


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


def pending_decisions(data_source_id):
    """구분=결정 이고 이슈 링크가 비어 있는 페이지 목록"""
    body = {
        "filter": {
            "and": [
                {"property": KIND_PROP, "select": {"equals": KIND_VALUE}},
                {"property": ISSUE_PROP, "url": {"is_empty": True}},
            ]
        },
        "page_size": 100,
    }
    pages = []
    while True:
        res = notion("POST", f"/data_sources/{data_source_id}/query", body)
        pages += res["results"]
        if not res.get("has_more"):
            return pages
        body["start_cursor"] = res["next_cursor"]


def plain(rich_text):
    return "".join(t.get("plain_text", "") for t in rich_text or [])


def page_title(page):
    for prop in page["properties"].values():
        if prop["type"] == "title":
            return plain(prop["title"]).strip() or "(제목 없음)"
    return "(제목 없음)"


def page_body(page_id):
    """페이지 본문 첫 단계 블록을 마크다운 비슷하게 변환 (최대 100블록)"""
    res = notion("GET", f"/blocks/{page_id}/children?page_size=100")
    prefix = {
        "heading_1": "## ", "heading_2": "### ", "heading_3": "#### ",
        "bulleted_list_item": "- ", "numbered_list_item": "1. ",
        "quote": "> ", "callout": "> ",
    }
    lines = []
    for block in res.get("results", []):
        kind = block["type"]
        content = block.get(kind, {})
        if "rich_text" not in content:
            continue
        text = plain(content["rich_text"])
        if kind == "to_do":
            lines.append(f"- [{'x' if content.get('checked') else ' '}] {text}")
        elif kind == "code":
            lines.append(f"```\n{text}\n```")
        else:
            lines.append(prefix.get(kind, "") + text)
    return "\n".join(lines).strip()


def describe(page):
    props = page["properties"]
    rows = []
    people = [p["name"] for p in props.get("담당자", {}).get("people", []) if p.get("name")]
    if people:
        rows.append(f"- 담당자: {', '.join(people)}")
    date = props.get("일정", {}).get("date")
    if date:
        rows.append(f"- 일정: {date['start']}" + (f" ~ {date['end']}" if date.get("end") else ""))
    parent = props.get("상위 항목", {}).get("relation", [])
    if parent:
        rows.append(f"- 상위 항목: https://www.notion.so/{parent[0]['id'].replace('-', '')}")
    rows.append(f"- 노션: {page['url']}")
    return "\n".join(rows)


def existing_issues(repo):
    """이미 만든 이슈 (노션 링크 저장 실패 시 중복 생성 방지용)"""
    found, page = {}, 1
    while True:
        issues = github("GET", f"/repos/{repo}/issues?labels={LABEL}&state=all&per_page=100&page={page}")
        for issue in issues:
            body = issue.get("body") or ""
            if f"<!-- {MARKER}:" in body:
                pid = body.split(f"<!-- {MARKER}:", 1)[1].split("-->", 1)[0].strip()
                found[pid] = issue["html_url"]
        if len(issues) < 100:
            return found
        page += 1


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    dry_run = os.environ.get("DRY_RUN") == "1"
    pages = pending_decisions(os.environ["NOTION_DATA_SOURCE_ID"])
    print(f"등록 대기 중인 결정: {len(pages)}건")
    if not pages:
        return
    known = {} if dry_run else existing_issues(repo)

    for page in pages:
        title = page_title(page)
        if dry_run:
            print(f"[DRY RUN] {title}")
            continue
        url = known.get(page["id"])
        if url:
            print(f"이미 있는 이슈 연결: {title} → {url}")
        else:
            content = page_body(page["id"])
            body = f"노션 일정 관리에서 등록된 결정입니다.\n\n{describe(page)}\n"
            if content:
                body += f"\n---\n\n{content}\n"
            body += f"\n<!-- {MARKER}: {page['id']} -->"
            issue = github("POST", f"/repos/{repo}/issues", {
                "title": f"[결정] {title}",
                "body": body,
                "labels": [LABEL],
            })
            url = issue["html_url"]
            print(f"이슈 생성: {title} → {url}")
        notion("PATCH", f"/pages/{page['id']}", {"properties": {ISSUE_PROP: {"url": url}}})


if __name__ == "__main__":
    main()
