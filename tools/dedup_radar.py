# -*- coding: utf-8 -*-
"""radar.db 에서 같은 공고 번호(사람인 rec_idx · 잡코리아 GI_Read · 인크루트 job)를 가진 '진행 중' 행을 하나로 합친다.

기본은 미리보기(아무것도 쓰지 않음). --apply 를 주면 DB 를 먼저 radar.db.bak_<시각> 으로 복사한 뒤 쓴다.
남길 행: 원문 확인(verified_at) 있는 행 > 내 표시(mark) 있는 행 > 처음 본 날이 이른 행.
나머지 행은 지우지 않고 status='closed', verify='중복: <남긴 id>' 로 닫는다(되돌리기 쉬움).
남길 행에는 first_seen(가장 이른 값)·last_seen(가장 늦은 값)·비어 있는 mark/원문 확인 칸을 채운다.

사용: python tools/dedup_radar.py [--db <경로>] [--apply]
주의: 수집(job_radar.py)이 도는 중에는 --apply 하지 말 것 (run.lock 이 있으면 멈춤).
"""
import argparse
import datetime as dt
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import job_radar as jr  # noqa: E402  (canon_key 규칙을 수집기와 똑같이 쓰기 위해)

FILL = ("mark", "verify", "verified_at", "location", "edu", "emp_type", "deadline_time", "company_full",
        "duty", "duty_evidence", "duty_flag", "csn", "company_form", "cmp_salary", "cmp_size")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(jr.DB_PATH))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    db = Path(a.db)
    if a.apply and (db.parent / "run.lock").exists():
        sys.exit(f"수집 실행 중(run.lock 있음): {db.parent / 'run.lock'} — 끝난 뒤 다시 실행하세요.")
    con = sqlite3.connect(db) if a.apply else sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)  # 미리보기는 읽기 전용
    con.row_factory = sqlite3.Row
    cols = [c[1] for c in con.execute("PRAGMA table_info(jobs)")]
    groups = {}
    for r in con.execute("SELECT * FROM jobs WHERE status='open'"):
        k = jr.canon_key(r["link"])
        if k:
            groups.setdefault(k, []).append(dict(r))
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"진행 중 행 중 공고 번호가 같은 묶음: {len(dups)}개 (닫을 행 {sum(len(v) - 1 for v in dups.values())}개)")
    plan = []
    for k, rows in sorted(dups.items()):
        # 같은 공고가 여러 회사 소스('(검색)'·계열사 페이지)에서 다른 회사명으로 들어온 경우가 많다 →
        # 원문 정식 회사명(company_full)과 회사명이 맞는 행을 먼저 남긴다
        full = jr.norm_co(next((r.get("company_full") for r in rows if r.get("company_full")), ""))
        same = lambda r: bool(full) and bool(jr.norm_co(r["company"])) and (jr.norm_co(r["company"]) in full or full in jr.norm_co(r["company"]))
        rows.sort(key=lambda r: (not same(r), -len(jr.norm_co(r["company"])) if same(r) else 0, not r.get("verified_at"), not r.get("mark"), r.get("first_seen") or ""))
        keep, rest = rows[0], rows[1:]
        upd = {"first_seen": min(r["first_seen"] or "9999" for r in rows), "last_seen": max(r["last_seen"] or "" for r in rows)}
        for c in FILL:
            if c in cols and not keep.get(c):
                v = next((r[c] for r in rest if r.get(c)), None)
                if v:
                    upd[c] = v
        plan.append((keep["id"], upd, [r["id"] for r in rest]))
        print(f"\n[{k}] 남김 {keep['id']} | {keep['company']} | {keep['title'][:50]}")
        for r in rest:
            print(f"   닫음 {r['id']} | {r['company']} | {r['title'][:50]} (처음 {r['first_seen']})")
        extra = {c: v for c, v in upd.items() if c not in ("first_seen", "last_seen")}
        if extra:
            print(f"   채움: {', '.join(extra)}")
    if not a.apply:
        print("\n미리보기만 했습니다. 실제로 합치려면 --apply")
        return
    if not plan:
        return
    bak = db.with_name(f"{db.name}.bak_{dt.datetime.now():%Y%m%d_%H%M%S}")
    con.close()
    shutil.copy2(db, bak)
    print(f"\n백업: {bak}")
    con = sqlite3.connect(db)
    for keep_id, upd, rest in plan:
        con.execute(f"UPDATE jobs SET {', '.join(f'{c}=?' for c in upd)} WHERE id=?", (*upd.values(), keep_id))
        con.executemany("UPDATE jobs SET status='closed', verify=? WHERE id=?", [(f"중복: {keep_id}", i) for i in rest])
    con.commit()
    print(f"적용: {len(plan)}개 묶음, {sum(len(p[2]) for p in plan)}개 행 닫음")


if __name__ == "__main__":
    main()
