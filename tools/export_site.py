# -*- coding: utf-8 -*-
"""radar.db → site/data/jobs.json (웹 페이지가 읽는 공고 목록). 수집 직후 매번 실행.
필드 이름은 예전 claude.ai 웹 레이더 DB(jobs 컬렉션)와 같게 맞춘다 → 화면 코드는 그대로.
분야(sec)는 저장값 대신 지금 규칙으로 다시 계산한다: 규칙을 고치면 다음 내보내기부터 바로 반영."""
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ml"))
import job_radar as jr  # noqa: E402  (DATA 폴더·설정 읽기 규칙을 그대로 쓴다)
import relevance as R  # noqa: E402

KEEP_CLOSED_DAYS = 30  # 닫힌 공고도 30일은 남긴다('마감됨' 보기·내 지원 현황 유지)
# 우량 중견 기준(사용자 지시 2026-10-08: "어중간한 중견은 빠지고 대기업 위주") — 사람인 기업정보(국민연금 사원수·평균연봉 추정)
GOOD_MID = {"employees": 1000, "avg_salary": 7000, "salary_min_employees": 300}  # 작은 회사의 사람인 연봉 추정치는 부정확 → 연봉 기준은 300명↑만
NORM = lambda s: re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\s|[()·.,（）\-]", "", s or "").lower()
GROUP_SHOW = {"에스케이": "SK", "엘지": "LG", "지에스": "GS", "씨제이": "CJ", "케이티": "KT", "엘에스": "LS", "에이치디씨": "HDC", "디엘": "DL", "에쓰-오일": "S-OIL", "케이씨씨": "KCC", "엘엑스": "LX", "에이치엘": "HL", "에스케이디스커버리": "SK디스커버리"}


def load_large_groups():
    """공정위 지정 대기업집단 소속회사 명단(data/large_groups.json) → {정규화 회사명: 그룹명}. 없으면 빈 사전."""
    p = ROOT / "data" / "large_groups.json"
    if not p.exists():
        return {}
    out = {}
    for g in json.loads(p.read_text(encoding="utf-8")).get("groups", []):
        # 회사명 전체가 같을 때만 계열사로 본다(별칭 '에스케이→SK' 등 포함). 부분 일치는 엉뚱한 회사를 잡는다
        for a in g.get("affiliates_detail") or [{"name": x} for x in g.get("affiliates", [])]:
            for n in [a.get("name"), a.get("norm"), *a.get("aliases", [])]:
                if n:
                    out[NORM(n)] = GROUP_SHOW.get(g["group"], g["group"])
    return out


# 헤드헌팅·파견·인력 회사 이름 (사람인·잡코리아 모음 검색은 '헤드헌팅' 표시 없이 들어온다 — 2026-10-09 실측)
AGENCY = re.compile(r"서치|써치|헤드헌|파트너스|스카우트|커리어|에이치알|\bHR\b|피플|맨파워|퍼솔|아데코|인드림|헌터|휴먼|리크루트|아웃소싱|인재|잡$", re.I)


def co_tier(r, size, groups, mid_names=()):
    """'대기업' / '우량 중견' / '중견·기타'. 화면은 '중견·기타'를 기본으로 숨긴다(데이터는 남김)."""
    for n in (r.get("company_full"), r.get("company")):
        if n and NORM(n) in groups:
            return "대기업", groups[NORM(n)]
    rank = r.get("size_rank") or 5
    if "헤드헌팅" in size or "→" in (r.get("company") or "") or AGENCY.search(r.get("company") or ""):
        # 헤드헌팅: 고객사 설명으로 판단 ('대기업 본사', '일본계 대기업', '코스피상장사', '외국계 화학사' 는 보여 주고 '중견'·'비공개'는 숨김)
        desc = (r.get("company") or "") + " " + (r.get("title") or "")
        if re.search(r"대기업|대기업\s*계열|그룹|코스피|상장|글로벌|외국계|다국적", desc) and not re.search(r"중견|중소", desc):
            return "대기업", ""
        return "중견·기타", ""
    if rank <= 3 or "대기업" in size or re.search(r"공사|공단|공공기관", r.get("company") or ""):
        return "대기업", ""
    emp, sal = r.get("employees") or 0, r.get("avg_salary") or 0
    if emp >= GOOD_MID["employees"] or (sal >= GOOD_MID["avg_salary"] and emp >= GOOD_MID["salary_min_employees"]):
        return "우량 중견", ""
    # 회사 정보를 아직 못 읽은 회사: 직접 고른 '중견 목록'(settings.mid_companies)에 있는 회사만 일단 보여 준다(재현율)
    if not emp and not sal and any(m and m in (r.get("company") or "") for m in mid_names):
        return "우량 중견", ""
    return "중견·기타", ""


def short(v, n=40):
    """화면 글자에서 잘못 읽힌 긴 값(예: 경력 칸에 전형단계·접수기간까지 들어간 483자) → 앞 토막만. 2026-10-08 S-OIL 실례"""
    v = (v or "").strip()
    if len(v) <= n:
        return v
    import re
    return re.split(r"\s(?:전형|근무|접수|학력|우대|마감)", v)[0][:n].strip()


def main(out=ROOT / "site" / "data" / "jobs.json"):
    st = jr.load_settings()
    con = sqlite3.connect(jr.DB_PATH)
    con.row_factory = sqlite3.Row
    t = jr.today()
    cutoff = (t - dt.timedelta(days=KEEP_CLOSED_DAYS)).isoformat()
    groups = load_large_groups()
    mids = [m for m in st.get("mid_companies", []) if len(m) >= 2]
    jobs = []
    for r in con.execute("SELECT * FROM jobs WHERE status='open' OR last_seen>=? ORDER BY first_seen DESC", (cutoff,)):
        r = dict(r)
        if (r.get("verify") or "").startswith(("중복: ", "다른 회사")):
            continue
        sec = R.sections({"title": r["title"], "company": r["company"]}, st, r.get("size_rank"))
        if not sec:  # 규칙이 바뀌어 어느 분야에도 안 맞는 옛 공고
            continue
        sc = r.get("rel_score") or 0
        # 규모: 회사 목록에 없던 회사는 사람인 기업정보(기업형태)로 올린다 — 모음 검색으로 들어온 대기업 계열사 등
        size, form = r.get("size") or "", r.get("company_form") or ""
        if (r.get("size_rank") or 5) >= 4 and "대기업" in form:
            size = "대기업"
        elif (r.get("size_rank") or 5) == 5 and "중견" in form:
            size = "중견"
        tier, grp = co_tier(r, size, groups, mids)
        if grp and not re.search(r"대기업", size):
            size = "대기업 계열"  # 공정위 명단으로 확인된 계열사
        jobs.append({
            "id": r["id"], "sec": sec, "status": r["status"], "co_tier": tier, "group": grp,
            "company": r["company"], "company_full": r.get("company_full") or "", "size": size,
            "track": r.get("track") or "경력", "title": r["title"], "exp": short(r.get("exp")), "edu": short(r.get("edu")),
            "emp_type": short(r.get("emp_type"), 60), "location": short(r.get("location"), 80), "posted": r.get("posted") or "",
            "deadline": r.get("deadline") or "", "deadline_time": r.get("deadline_time") or "",
            "link": r["link"] if re.match(r"https?://", r.get("link") or "") else "",  # http(s) 만 내보낸다(보안)
            "fit": "상" if sc >= 0.6 else "중" if sc >= 0.3 else "하", "tier": r.get("tier") or "",
            "reason": f"모델 점수 {round(sc * 100)}" + (" · 원문 직무 의심" if r.get("duty") == "의심" else ""),
            "verify": r.get("verify") or "원문 확인 필요", "duty": r.get("duty") or "", "duty_evidence": r.get("duty_evidence") or "",
            "duty_flag": r.get("duty_flag") or "", "source": r.get("source") or "", "snippet": (r.get("snippet") or "")[:300],
            "avg_salary": r.get("avg_salary") or 0, "employees": r.get("employees") or 0, "salary_year": r.get("salary_year") or 0,
            "company_form": r.get("company_form") or "", "firstSeen": r.get("first_seen") or "", "lastSeen": r.get("last_seen") or "",
        })
    run = con.execute("SELECT run_at, new, open, sources_ok, sources_total FROM runs ORDER BY run_at DESC LIMIT 1").fetchone()
    # runDate = 이번 수집이 '처음 본 날'로 적은 날짜(수집 시작일). 끝난 시각의 날짜를 쓰면 자정을 넘긴 수집에서 새 공고가 0건으로 보인다(2026-10-09 실례)
    first = max((j["firstSeen"] for j in jobs if j["firstSeen"]), default="")
    meta = {"runDate": first or (run[0][:10] if run else t.isoformat()), "runTime": run[0][11:16] if run else "", "runAt": run[0] if run else "",
            "new": run[1] if run else 0, "open": run[2] if run else 0,
            "sourcesOk": run[3] if run else 0, "sourcesTotal": run[4] if run else 0,
            "counts": {s: sum(s in j["sec"] and j["status"] == "open" for j in jobs) for s in ("환경", "안전")}}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": meta, "jobs": jobs}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"site 데이터: 공고 {len(jobs)}건 (진행 중 환경 {meta['counts']['환경']} · 안전 {meta['counts']['안전']}) → {out}")


if __name__ == "__main__":
    main()
