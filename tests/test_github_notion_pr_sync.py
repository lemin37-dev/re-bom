"""`.github/scripts/notion_pr_sync.py` (PR → 노션 일정 관리) 테스트. 노션은 호출하지 않는다."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "notion_pr_sync.py"
spec = importlib.util.spec_from_file_location("notion_pr_sync", SCRIPT)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

DS = "363b95a0-bdee-8249-b2f1-87c03f2a8871"
PAGE_HEX = "4a1b95a0bdee81bda7eee1b78d7c2552"
PAGE_ID = "4a1b95a0-bdee-81bd-a7ee-e1b78d7c2552"
PR_URL = "https://github.com/lemin37-dev/re-bom/pull/80"


def page(status="대기", pr_url=None, parent_ds=DS):
    return {
        "id": PAGE_ID,
        "parent": {"type": "data_source_id", "data_source_id": parent_ds},
        "properties": {
            "이름": {"type": "title", "title": [{"plain_text": "Airflow 수집 DAG"}]},
            "상태": {"type": "status", "status": {"name": status} if status else None},
            "GitHub PR": {"type": "url", "url": pr_url},
        },
    }


@pytest.mark.parametrize(
    "body",
    [
        f"- 관련 이슈·노션: https://www.notion.so/Airflow-DAG-{PAGE_HEX}",
        f"[일정](https://app.notion.com/p/{PAGE_HEX}?pvs=4)",
        f"https://myteam.notion.site/{PAGE_ID}",
        f"https://www.notion.so/ws/{PAGE_HEX}?v=0123456789abcdef0123456789abcdef",
    ],
)
def test_extract_page_ids_link_forms(body):
    assert sync.extract_page_ids(body) == [PAGE_ID]


def test_extract_page_ids_skips_comments_duplicates_and_other_sites():
    body = (
        "<!-- 예) https://www.notion.so/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->\n"
        f"https://www.notion.so/{PAGE_HEX} https://www.notion.so/x-{PAGE_HEX}\n"
        "https://github.com/x/y/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    )
    assert sync.extract_page_ids(body) == [PAGE_ID]
    assert sync.extract_page_ids(None) == []


@pytest.mark.parametrize(
    "action,merged,current,expected",
    [
        ("opened", False, "대기", "진행 중"),
        ("opened", False, None, "진행 중"),
        ("ready_for_review", False, "시작 전", "진행 중"),
        ("opened", False, "보류", None),  # 사람이 보류한 건 건드리지 않음
        ("reopened", False, "완료", None),
        ("edited", False, "진행 중", None),
        ("closed", True, "진행 중", "완료"),
        ("closed", True, "완료", None),
        ("closed", False, "진행 중", None),  # 머지 없이 닫힘
        ("labeled", False, "대기", None),
    ],
)
def test_target_status(action, merged, current, expected):
    assert sync.target_status(action, merged, current) == expected


def test_build_update_sets_status_and_pr_link():
    assert sync.build_update(page(), PR_URL, "opened", False) == {
        "properties": {"상태": {"status": {"name": "진행 중"}}, "GitHub PR": {"url": PR_URL}}
    }
    assert sync.build_update(page("진행 중", PR_URL), PR_URL, "edited", False) is None


def test_in_data_source_accepts_dashless_and_rejects_other_parent():
    assert sync.in_data_source(page(parent_ds=DS.replace("-", "")), DS)
    assert not sync.in_data_source(page(parent_ds="00000000-0000-0000-0000-000000000000"), DS)
    assert not sync.in_data_source({"parent": {"type": "page_id", "page_id": "x"}}, DS)


def run_main(tmp_path, monkeypatch, body, pages, action="closed", merged=True, dry_run=False, fresh_body=None, pr_extra=None, dup_by_url=False):
    pr = {"number": 80, "html_url": PR_URL, "merged": merged, "body": body, "title": "feat(ingest): 증분 수집",
          "created_at": "2026-11-23T16:30:00Z", "merged_at": "2026-11-25T02:00:00Z" if merged else None,
          "user": {"type": "User"}}
    pr.update(pr_extra or {})
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"action": action, "pull_request": pr}), encoding="utf-8")
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_REPOSITORY", "lemin37-dev/re-bom")
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", DS)
    monkeypatch.setenv("DRY_RUN", "1" if dry_run else "0")
    calls = []

    def fake_notion(method, path, body=None):
        calls.append(("notion", method, path, body))
        if method == "GET":
            return pages[path.rsplit("/", 1)[1]]
        if path.endswith("/query"):
            return {"results": [page()] if dup_by_url else []}
        if method == "POST":
            return {"url": f"https://www.notion.so/{PAGE_HEX}"}
        return {}

    def fake_github(method, path, body=None):
        calls.append(("github", method, path, body))
        if method == "GET":
            return {"body": pr["body"] if fresh_body is None else fresh_body}
        return {}

    monkeypatch.setattr(sync, "notion", fake_notion)
    monkeypatch.setattr(sync, "github", fake_github)
    sync.main()
    return calls, summary.read_text(encoding="utf-8")


def test_main_merged_pr_marks_done(tmp_path, monkeypatch):
    other = "11111111-2222-3333-4444-555555555555"
    body = f"설계: https://www.notion.so/{other.replace('-', '')}\n일정: https://www.notion.so/{PAGE_HEX}"
    pages = {other: page(parent_ds="99999999-0000-0000-0000-000000000000"), PAGE_ID: page("진행 중")}
    calls, summary = run_main(tmp_path, monkeypatch, body, pages)
    writes = [c for c in calls if c[1] != "GET"]
    assert writes == [
        ("notion", "PATCH", f"/pages/{PAGE_ID}",
         {"properties": {"상태": {"status": {"name": "완료"}}, "GitHub PR": {"url": PR_URL}}})
    ]
    assert "Airflow 수집 DAG" in summary and "완료" in summary


def test_main_dry_run_does_not_write(tmp_path, monkeypatch):
    calls, summary = run_main(tmp_path, monkeypatch, f"https://www.notion.so/{PAGE_HEX}", {PAGE_ID: page()},
                              action="opened", merged=False, dry_run=True)
    assert [c[1] for c in calls] == ["GET"]
    assert "[DRY RUN]" in summary


def test_main_no_link_creates_task_and_appends_link(tmp_path, monkeypatch):
    calls, summary = run_main(tmp_path, monkeypatch, "## 요약\n증분 수집", {}, action="opened", merged=False)
    create = [c for c in calls if c[:3] == ("notion", "POST", "/pages")]
    assert len(create) == 1
    props = create[0][3]["properties"]
    assert create[0][3]["parent"] == {"type": "data_source_id", "data_source_id": DS}
    assert props["이름"]["title"][0]["text"]["content"] == "feat(ingest): 증분 수집"
    assert props["구분"] == {"select": {"name": "작업"}}
    assert props["상태"] == {"status": {"name": "진행 중"}}
    assert props["GitHub PR"] == {"url": PR_URL}
    assert props["일정"] == {"date": {"start": "2026-11-24", "end": None}}  # UTC 16:30 → KST 다음날
    patch = [c for c in calls if c[:2] == ("github", "PATCH")]
    assert patch[0][2] == "/repos/lemin37-dev/re-bom/pulls/80"
    assert patch[0][3]["body"].endswith(f"노션 일정: https://www.notion.so/{PAGE_HEX}\n")
    assert sync.extract_page_ids(patch[0][3]["body"]) == [PAGE_ID]
    assert "새 작업 항목 생성" in summary


def test_main_merged_without_link_creates_done_task_with_range(tmp_path, monkeypatch):
    calls, _ = run_main(tmp_path, monkeypatch, "", {})
    props = [c for c in calls if c[:3] == ("notion", "POST", "/pages")][0][3]["properties"]
    assert props["상태"] == {"status": {"name": "완료"}}
    assert props["일정"] == {"date": {"start": "2026-11-24", "end": "2026-11-25"}}


def test_main_no_link_skips_when_not_needed(tmp_path, monkeypatch):
    calls, _ = run_main(tmp_path, monkeypatch, "", {}, merged=False)  # 머지 없이 닫힘
    assert calls == []
    calls, _ = run_main(tmp_path, monkeypatch, "", {}, action="opened", merged=False, pr_extra={"user": {"type": "Bot"}})
    assert calls == []
    # 앞선 실행이 이미 링크를 붙였으면 다시 만들지 않는다
    calls, summary = run_main(tmp_path, monkeypatch, "", {}, action="edited", merged=False,
                              fresh_body=f"노션 일정: https://www.notion.so/{PAGE_HEX}")
    assert [c[:2] for c in calls] == [("github", "GET"), ("notion", "POST")]  # 최신 본문 + PR 링크 조회만
    assert "이미 연결됨" in summary


@pytest.mark.parametrize(
    "pr,expected",
    [
        ({"state": "open", "merged_at": None}, ("opened", False)),
        ({"state": "closed", "merged_at": "2026-11-20T01:00:00Z"}, ("closed", True)),
        ({"state": "closed", "merged_at": None}, ("closed", False)),
    ],
)
def test_backfill_event(pr, expected):
    assert sync.backfill_event(pr) == expected


def test_backfill_lists_all_prs_and_applies_rules(tmp_path, monkeypatch):
    linked = {"number": 1, "state": "closed", "merged_at": "2026-11-20T01:00:00Z", "title": "링크 있음",
              "html_url": PR_URL, "body": f"https://www.notion.so/{PAGE_HEX}", "created_at": "2026-11-19T01:00:00Z",
              "user": {"type": "User"}}
    merged_no_link = dict(linked, number=2, title="링크 없음 머지", body="", html_url=PR_URL + "2")
    closed_no_link = dict(merged_no_link, number=3, merged_at=None, title="머지 없이 닫힘")
    open_no_link = dict(merged_no_link, number=4, state="open", merged_at=None, title="열린 PR")
    prs = [linked, merged_no_link, closed_no_link, open_no_link]
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_REPOSITORY", "lemin37-dev/re-bom")
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", DS)
    monkeypatch.setenv("DRY_RUN", "0")
    monkeypatch.delenv("BACKFILL_STATE", raising=False)
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def fake_github(method, path, body=None):
        calls.append(("github", method, path, body))
        if path.startswith("/repos/lemin37-dev/re-bom/pulls?"):
            return prs
        if method == "GET":
            return {"body": ""}
        return {}

    def fake_notion(method, path, body=None):
        calls.append(("notion", method, path, body))
        if method == "GET":
            return page("진행 중")
        if path.endswith("/query"):
            return {"results": []}
        if method == "POST":
            return {"url": "https://www.notion.so/new"}
        return {}

    monkeypatch.setattr(sync, "github", fake_github)
    monkeypatch.setattr(sync, "notion", fake_notion)
    sync.main()

    assert calls[0][2] == "/repos/lemin37-dev/re-bom/pulls?state=all&sort=created&direction=asc&per_page=100&page=1"
    patch_done = [c for c in calls if c[:3] == ("notion", "PATCH", f"/pages/{PAGE_ID}")]
    assert patch_done[0][3]["properties"]["상태"] == {"status": {"name": "완료"}}
    created = [c[3]["properties"] for c in calls if c[:3] == ("notion", "POST", "/pages")]
    assert [p["이름"]["title"][0]["text"]["content"] for p in created] == ["링크 없음 머지", "열린 PR"]
    assert [p["상태"]["status"]["name"] for p in created] == ["완료", "진행 중"]
    pr_edits = [c[2] for c in calls if c[:2] == ("github", "PATCH")]
    assert pr_edits == ["/repos/lemin37-dev/re-bom/pulls/2", "/repos/lemin37-dev/re-bom/pulls/4"]
    text = summary.read_text(encoding="utf-8")
    assert "PR 4개" in text and "머지 없이 닫힘" in text


def test_main_reuses_item_with_same_pr_url(tmp_path, monkeypatch):
    calls, summary = run_main(tmp_path, monkeypatch, "", {}, dup_by_url=True)
    assert not [c for c in calls if c[:3] == ("notion", "POST", "/pages")]
    assert [c[2] for c in calls if c[1] == "PATCH"] == [f"/pages/{PAGE_ID}"]
    assert "같은 항목에 연결" in summary


def test_meeting_item_gets_link_but_not_status():
    meeting = page("진행 중")
    meeting["properties"]["구분"] = {"type": "select", "select": {"name": "회의"}}
    assert sync.build_update(meeting, PR_URL, "closed", True) == {"properties": {"GitHub PR": {"url": PR_URL}}}
