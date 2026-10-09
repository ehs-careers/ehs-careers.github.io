"""마이다스 잡플렉스(recruiter.co.kr) 채용 사이트 공고 목록 — 표준 라이브러리만 사용.

※ 기본값으로는 호출하지 않는다(robots.txt 때문). 아래 '상태'를 먼저 읽을 것.

엔드포인트(번들 분석, 2026-10-08 — 실제 호출로 검증하지 않음):
  POST https://api-recruiter.recruiter.co.kr/position/v1/jobflex
    헤더  prefix: <tenant>.recruiter.co.kr   (화면 axios 가 window.location.hostname 을 넣음 → 회사 식별)
    본문  {"pageableRq": {"page": N, "size": S},
           "filter": {"keyword": "", "tagSnList": [], "jobGroupSnList": [], "careerTypeList": [],
                      "regionSnList": [], "submissionStatusList": [], "openStatusList": ["OPEN"],
                      "resumeLanguageTypeList": []}}
    응답  {list: [...], pagination: {page, totalPages, ...}}  (화면 코드가 e.list / e.pagination 사용)
    항목  positionSn · title · startDateTime · endDateTime · dday · careerType · recruitmentType
          (PERMANENT=상시) · jobGroupName · workingAreaName · submissionStatus · tagList
  - 근거: /_next/static/chunks/7981-*.js (목록 호출), 7414-*.js 모듈 35434(API 호스트 표),
          모듈 82900(axios: baseURL + prefix 헤더), 2767-*.js(목록 카드 필드).
  - 공고 원문: https://<tenant>.recruiter.co.kr/career/jobs/<positionSn>
  - 회사 화면(/career/home, /career/jobs)은 목록을 서버에서 그리지 않음(빈 껍데기) → API 외 경로 없음.

상태(robots.txt):
  - <tenant>.recruiter.co.kr/robots.txt : Allow: / (단 /app*, /bbs*, /resources* 금지 → 구형 /app/jobnotice/... 불가)
  - api-recruiter.recruiter.co.kr/robots.txt : "User-agent: *  Disallow: /"  ← 목록 API 전체 금지
  → fetch() 는 API 호스트 robots.txt 를 매번 확인하고 금지면 RobotsDisallowed 를 낸다.
    사용자가 '브라우저가 부르는 것과 같은 1회성 조회'로 허용하기로 정하면
    환경변수 JOBRADAR_RECRUITER_IGNORE_ROBOTS=1 로만 켤 수 있다(기본 꺼짐).
  - hyosung.recruiter.co.kr 은 구형 플랫폼이고 robots.txt 가 사이트 전체 Disallow → 대상 아님.
"""
import gzip
import json
import os
import re
import sys
import time
import urllib.request
import urllib.robotparser

API_HOST = "https://api-recruiter.recruiter.co.kr"
LIST_PATH = "/position/v1/jobflex"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
TIMEOUT = 20
DELAY = 4.0
PAGE_SIZE = 50
MAX_PAGES = 20

# (tenant, company) — 2026-10-08 회사 화면(200) 존재 확인.
TENANTS = [
    ("s-oil", "S-OIL"),
    ("celltrion", "셀트리온"),
    ("nexentire", "넥센타이어"),
    ("kpgroup", "한국석유공업"),
]
# 참고: hyosung(효성) — 구형 플랫폼 + robots 전체 금지라 제외.


class RobotsDisallowed(PermissionError):
    pass


_last = {}


def _wait(host):
    w = DELAY - (time.time() - _last.get(host, 0.0))
    if w > 0:
        time.sleep(w)


def _open(req):
    host = req.full_url.split("/")[2]
    _wait(host)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # HTTPError 는 그대로 올라감
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
    finally:
        _last[host] = time.time()
    return raw.decode("utf-8", "replace")


_robots_cache = {}


def _robots_allows(url):
    host = "/".join(url.split("/")[:3])
    rp = _robots_cache.get(host)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            txt = _open(urllib.request.Request(host + "/robots.txt", headers={"User-Agent": UA}))
            rp.parse(txt.splitlines())
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                rp.parse([])            # robots 없음 = 허용
            else:
                rp.parse(["User-agent: *", "Disallow: /"])
        _robots_cache[host] = rp
    return rp.can_fetch("*", url)


def _post_list(tenant, page):
    host = f"{tenant}.recruiter.co.kr"
    body = {"pageableRq": {"page": page, "size": PAGE_SIZE},
            "filter": {"keyword": "", "tagSnList": [], "jobGroupSnList": [], "careerTypeList": [],
                       "regionSnList": [], "submissionStatusList": [], "openStatusList": ["OPEN"],
                       "resumeLanguageTypeList": []}}
    req = urllib.request.Request(
        API_HOST + LIST_PATH, data=json.dumps(body).encode("utf-8"),
        headers={"User-Agent": UA, "Content-Type": "application/json",
                 "Accept": "application/json, text/plain, */*", "Accept-Encoding": "gzip",
                 "Accept-Language": "ko-KR,ko;q=0.9", "prefix": host,
                 "Origin": f"https://{host}", "Referer": f"https://{host}/career/jobs"})
    data = json.loads(_open(req))
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise RuntimeError(f"recruiter 응답 형식이 바뀜: {str(data)[:200]}")
    return data


_CAREER = {"NEW": "신입", "NEWCOMER": "신입", "CAREER": "경력", "EXPERIENCED": "경력",
           "IRRELEVANT": "경력무관", "ANY": "경력무관", "NEW_OR_CAREER": "신입/경력"}


def _block(p):
    parts = []
    s, e = p.get("startDateTime"), p.get("endDateTime")
    if p.get("recruitmentType") == "PERMANENT":
        parts.append(f"상시채용 (시작 {s or '?'})")
    else:
        parts.append(f"접수 {s or '?'} ~ {e or '?'}")
    ct = p.get("careerType")
    if ct:
        parts.append("경력구분 " + _CAREER.get(str(ct).upper(), str(ct)))
    if p.get("workingAreaName"):
        parts.append(f"근무지 {p['workingAreaName']}")
    if p.get("jobGroupName"):
        parts.append(f"직군 {p['jobGroupName']}")
    tags = [t.get("name") if isinstance(t, dict) else str(t) for t in (p.get("tagList") or [])]
    tags = [t for t in tags if t]
    if tags:
        parts.append("태그 " + "·".join(tags))
    return " | ".join(parts)


def fetch(tenant: str, company: str) -> list:
    """tenant(예: 's-oil')의 진행 중 공고 전체. 기본값에서는 robots 금지로 RobotsDisallowed."""
    url = API_HOST + LIST_PATH
    # 사용자 지시(2026-10-08): robots 금지와 관계없이 수집. 하루 1회·요청 간격 유지. 끄려면 JOBRADAR_RECRUITER_IGNORE_ROBOTS=0
    if not _robots_allows(url) and os.environ.get("JOBRADAR_RECRUITER_IGNORE_ROBOTS", "1") != "1":
        raise RobotsDisallowed(f"{API_HOST}/robots.txt 가 {LIST_PATH} 를 금지함 — 호출하지 않음")
    seen, out = set(), []
    page, total_pages = 1, None
    while page <= MAX_PAGES:
        data = _post_list(tenant, page)
        rows = data["list"]
        new = 0
        for p in rows:
            sn = p.get("positionSn")
            if sn in seen:
                continue
            seen.add(sn)
            new += 1
            out.append({"company": company, "title": (p.get("title") or "").strip(),
                        "href": f"https://{tenant}.recruiter.co.kr/career/jobs/{sn}",
                        "block": _block(p)})
        pg = data.get("pagination") or {}
        total_pages = pg.get("totalPages", total_pages)
        if not rows or not new or (total_pages is not None and page >= total_pages):
            break
        page += 1
    return out


EHS = re.compile(r"환경|안전|EHS|SHE|HSE|보건|화학물질|PSM")

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    total = hits = 0
    for t, c in TENANTS:
        try:
            rows = fetch(t, c)
        except RobotsDisallowed as e:
            print(f"[SKIP robots] {c} ({t}): {e}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"[ERR] {c} ({t}): {type(e).__name__}: {e}")
            continue
        m = [r for r in rows if EHS.search(r["title"] + " " + r["block"])]
        total += len(rows)
        hits += len(m)
        print(f"{c} ({t}): {len(rows)}건, EHS 일치 {len(m)}건")
        for r in m:
            print(f"   - {r['title']} | {r['block']} | {r['href']}")
    print(f"합계 {total}건, EHS 일치 {hits}건")
