# -*- coding: utf-8 -*-
"""(1회용 기록, 2026-10-08) 예전 claude.ai 웹 레이더 DB 의 jobs 중 radar.db 에 없는 공고를 state/radar.db 에 옮긴다.
그 공고들은 대부분 18:55 클라우드 알림(웹 검색)이 찾은 것 — PC 수집 대상(sources.json)에 없는 회사가 많다.
id 는 웹 문서 id 그대로 → 웹에서 남긴 관심·지원·휴지통 표시가 새 페이지에서도 그대로 맞는다.
사용: python tools/import_web_jobs.py <웹 jobs 덤프 폴더(문서마다 {id,data})>  (JOBRADAR_DATA=state)"""
import glob
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import job_radar as jr  # noqa: E402

nco = lambda s: re.sub(r"\(주\)|㈜|주식회사|\s|[()·.,（）]", "", s or "").lower()
nti = lambda s: re.sub(r"[^0-9a-z가-힣]", "", re.sub(r"\[[^\]]*\]", "", (s or "").lower()))[:30]
RANK = [("헤드헌팅", 5), ("외국계", 2), ("계열", 2), ("관계사", 2), ("대기업", 1), ("공기업", 3), ("공공", 3), ("중견", 4)]


def main(dump):
    con = jr.db_open()
    have_id = {r[0] for r in con.execute("SELECT id FROM jobs")}
    have_key = {(nco(c), nti(t)) for c, t in con.execute("SELECT company, title FROM jobs")}
    by_title = {}
    for c, t in con.execute("SELECT company, title FROM jobs"):
        by_title.setdefault(nti(t), []).append(nco(c))
    # '롯데엔지니어링플라스틱(롯데EP)' ↔ '롯데엔지니어링플라스틱'처럼 회사 표기만 다른 같은 공고 (2026-10-08 실례)
    same_co = lambda a, b: bool(a and b) and (a.startswith(b) or b.startswith(a))
    dup = lambda c, t: any(same_co(nco(c), x) for x in by_title.get(nti(t), []))
    have_canon = {jr.canon_key(l) for (l,) in con.execute("SELECT link FROM jobs") if jr.canon_key(l)}
    added = 0
    for f in glob.glob(os.path.join(dump, "*.json")):
        o = json.loads(Path(f).read_text(encoding="utf-8"))
        i, d = o.get("id", Path(f).stem), o.get("data", o)
        if i in have_id or (nco(d.get("company")), nti(d.get("title"))) in have_key or jr.canon_key(d.get("link")) in have_canon \
                or dup(d.get("company"), d.get("title")):
            continue
        size = d.get("size") or ""
        rank = next((r for k, r in RANK if k in size), 5)
        dl = d.get("deadline") or ""
        con.execute("""INSERT INTO jobs(id,company,title,track,exp,posted,deadline,link,source,snippet,first_seen,last_seen,status,mark,size_rank,size,rel_score,tier,
                       location,edu,emp_type,deadline_time,company_full,verify,duty,duty_evidence,duty_flag)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (i, d.get("company", ""), d.get("title", ""), d.get("track") or "경력", d.get("exp", ""), d.get("posted", ""),
                     dl if re.fullmatch(r"\d{4}-\d{2}-\d{2}|상시|채용시", dl) else "", d.get("link", ""), "클라우드 알림",
                     (d.get("reason") or "")[:300], d.get("firstSeen", ""), d.get("lastSeen", ""), d.get("status") or "open",
                     rank, size, {"상": 0.7, "중": 0.4}.get(d.get("fit"), 0.2), "추천" if d.get("fit") in ("상", "중") else "검토",
                     d.get("location", ""), d.get("edu", ""), d.get("emp_type", ""), d.get("deadline_time", ""), d.get("company_full", ""),
                     d.get("verify", ""), d.get("duty", ""), d.get("duty_evidence", ""), d.get("duty_flag", "")))
        added += 1
    con.commit()
    print("옮긴 공고", added, "건")


if __name__ == "__main__":
    main(sys.argv[1])
