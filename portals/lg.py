"""LG 채용 홈페이지(careers.lg.com) 공고 수집 — 표준 라이브러리만 사용.

엔드포인트 (2026-10-08 실측, 로그인 불필요; 프런트 번들 axios baseURL = https://api.careers.lg.com/rmk):
  - 목록: POST https://api.careers.lg.com/rmk/job/retrieveJobNoticesList
      JSON: {"lnbSearch":"","hashTagText":"","recDate":"CREATION_DATE","order":"DESC",
             "careerList":[],"companyCodeList":[],"desireLocList":[],"jobGroupList":[]}
      응답: {"status":"S","data":{"jobNoticeList":[...], "listCount":N}} — 한 번에 전체(페이지 없음)
  - 공고 직접 링크(프런트 라우트): https://careers.lg.com/apply/detail?id=<jobNoticeId>
robots.txt: careers.lg.com 은 SPA 가 index.html 을 돌려줌(규칙 없음), api 호스트는 404.
"""
import json
import re
import time
import urllib.request

API = "https://api.careers.lg.com/rmk"
LIST_URL = API + "/job/retrieveJobNoticesList"
SITE = "https://careers.lg.com"
TIMEOUT = 20
DELAY = 4.0
MAX_PAGES = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BASE_BODY = {"lnbSearch": "", "hashTagText": "", "recDate": "CREATION_DATE", "order": "DESC",
             "careerList": [], "companyCodeList": [], "desireLocList": [], "jobGroupList": []}

_last = [0.0]


def _post(url, payload):
    wait = DELAY - (time.monotonic() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": UA, "Content-Type": "application/json",
                 "Accept": "application/json, text/plain, */*",
                 "Origin": SITE, "Referer": SITE + "/", "Accept-Language": "ko-KR,ko;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8"))
    finally:
        _last[0] = time.monotonic()
    if d.get("status") != "S":
        raise RuntimeError(f"LG API status={d.get('status')} msg={d.get('msg')}")
    return d.get("data") or {}


def _block(x):
    groups = ", ".join(g for g in (x.get("jobGroupName"), x.get("jobGroupName2")) if g)
    parts = [
        x.get("companyName") or "",
        x.get("jobNoticeName") or "",
        f"경력구분: {x.get('careerTypeName') or ''} · 채용유형: {x.get('recruitTypeName') or ''}",
        f"마감: {x.get('recEndDateTime') or ''} (D-{x.get('recDateDiff')}) · 상태: {x.get('noticeStatusName') or x.get('noticeStatus') or ''}",
        f"직무: {groups}" if groups else "",
        f"근무지: {x.get('workLocationName') or ''}" + (f" 외 {x['workLocationCnt'] - 1}곳" if (x.get("workLocationCnt") or 0) > 1 else ""),
        f"태그: {x['hashtagText']}" if x.get("hashtagText") else "",
    ]
    return "\n".join(p for p in parts if p)


def fetch():
    data = _post(LIST_URL, BASE_BODY)
    rows = list(data.get("jobNoticeList") or [])
    total = data.get("listCount") or len(rows)
    # 현재 API 는 전체를 한 번에 준다. 혹시 나중에 잘라 주면 pageNo 로 이어 받는다(추정 파라미터).
    page = 1
    seen = {r.get("jobNoticeId") for r in rows}
    while len(rows) < total and page < MAX_PAGES:
        page += 1
        more = (_post(LIST_URL, {**BASE_BODY, "pageNo": page, "currentPage": page}).get("jobNoticeList") or [])
        new = [r for r in more if r.get("jobNoticeId") not in seen]
        if not new:
            break
        seen.update(r.get("jobNoticeId") for r in new)
        rows.extend(new)

    return [{
        "company": x.get("companyName") or "",
        "title": (x.get("jobNoticeName") or "").strip(),
        "href": f"{SITE}/apply/detail?id={x.get('jobNoticeId')}",
        "block": _block(x),
    } for x in rows]


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    pat = re.compile(r"환경|안전|EHS|SHE|HSE|보건|화학물질|PSM")
    jobs = fetch()
    print("count:", len(jobs))
    for j in jobs[:5]:
        print("-", j["company"], "|", j["title"], "|", j["href"])
        print("   ", j["block"].replace("\n", " / ")[:200])
    t = [j for j in jobs if pat.search(j["title"])]
    b = [j for j in jobs if pat.search(j["block"])]
    print(f"EHS match: title {len(t)}, block {len(b)}")
    for j in b:
        print("  *", j["company"], "|", j["title"])
