# -*- coding: utf-8 -*-
"""수집 직후 건강 검진: 이상하면 실패(종료 코드 1)로 끝내 GitHub 이 저장소 주인에게 메일을 보내게 한다.
기준(2026-10-08): ① 정상 소스 비율 60% 미만 ② 사람인 소스가 거의 다 실패(해외 IP 차단 의심) ③ 그룹 채용 API 절반 넘게 실패.
결과는 site/data/health.json 에도 남겨 웹 화면이 '수집 상태'를 보여 준다."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import job_radar as jr  # noqa: E402


def main():
    con = sqlite3.connect(jr.DB_PATH)
    row = con.execute("SELECT run_at, sources_ok, sources_total, health FROM runs ORDER BY run_at DESC LIMIT 1").fetchone()
    if not row:
        print("수집 기록 없음"); return 1
    run_at, ok, total, health = row[0], row[1], row[2], json.loads(row[3] or "[]")
    sar = [h for h in health if "saramin.co.kr" in h.get("url", "")]
    sar_ok = sum(1 for h in sar if h["ok"])
    api = [h for h in health if h.get("type") == "api"]
    api_ok = sum(1 for h in api if h["ok"])
    failed = [f"{h['name']}: {h.get('error', '')[:60]}" for h in health if not h["ok"] and "disabled" not in h.get("error", "")]
    problems = []
    if total and ok / total < 0.6:
        problems.append(f"정상 소스 {ok}/{total} (60% 미만)")
    if len(sar) >= 20 and sar_ok / len(sar) < 0.3:
        problems.append(f"사람인 {sar_ok}/{len(sar)}만 정상 — 해외 서버 차단 의심")
    if len(api) >= 4 and api_ok / len(api) < 0.5:
        problems.append(f"그룹 채용 API {api_ok}/{len(api)}만 정상")
    out = {"runAt": run_at, "ok": ok, "total": total, "saramin": [sar_ok, len(sar)], "api": [api_ok, len(api)],
           "problems": problems, "failed": failed[:80]}
    (ROOT / "site" / "data").mkdir(parents=True, exist_ok=True)
    (ROOT / "site" / "data" / "health.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[{run_at}] 정상 {ok}/{total} · 사람인 {sar_ok}/{len(sar)} · 그룹 API {api_ok}/{len(api)}")
    for p in problems:
        print("  ✗", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
