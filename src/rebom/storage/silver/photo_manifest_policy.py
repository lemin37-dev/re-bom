"""사진 Manifest 상태에 따른 B팀 처리 정책."""

def decide_photo_manifest(manifest):
    status = manifest.get("status")
    run_id = manifest.get("run_id")
    files = manifest.get("files")
    failed = manifest.get("failed")

    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("사진 run_id 누락")

    if not isinstance(files, list):
        raise ValueError("사진 files 형식 오류")

    if not isinstance(failed, list):
        raise ValueError("사진 failed 형식 오류")

    if status == "ok":
        decision = "APPLY"
        needs_review = bool(failed)

    elif status == "aborted":
        decision = "APPLY_PARTIAL"
        needs_review = True

    elif status == "in_progress":
        decision = "DEFER"
        needs_review = False

    else:
        raise ValueError(f"알 수 없는 사진 상태: {status}")

    return {
        "run_id": run_id,
        "status": status,
        "decision": decision,
        "apply_count": len(files) if decision != "DEFER" else 0,
        "failed_count": len(failed),
        "needs_review": needs_review,
    }
