"""그리팅(greetinghr) 채용 사이트 공고 목록 — 표준 라이브러리만 사용.

엔드포인트(인증 없음, GET 1회):
  https://<tenant>.career.greetinghr.com/ko/home
  Next.js(pages router) 서버 렌더링 결과의 <script id="__NEXT_DATA__"> JSON 안
  props.pageProps.dehydratedState.queries 중 queryKey == ["openings"] 의 state.data 가
  그 회사 사이트에 게시된 공고 전체 목록(페이지 나눔 없음, 2026-10-08 OCI 23건·SPC 18건 실측).
  - 항목: openingId · title · openDate/dueDate(UTC, null=상시) · workspaceDivision.division
          · openingJobPosition.openingJobPositions[]: workspaceJob.job(직무) · workspaceOccupation
            · workspacePlace(근무지) · jobPositionCareer.careerType(NEW_COMER/EXPERIENCED/...)
            · careerFrom/careerTo · jobPositionEmployment.employmentType
  - 공고 원문: https://<tenant>.career.greetinghr.com/ko/o/<openingId>
  - 홈에 공고 영역이 없는 회사는 디자인 JSON 안의 내부 페이지 경로를 최대 5개 더 열어 찾는다.
robots.txt: Allow: / (지원서 /o/*/apply, /a/*, /m/* 만 Disallow) — 여기서 쓰는 경로는 허용.
"""
import datetime as _dt
import gzip
import json
import re
import sys
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
TIMEOUT = 20
DELAY = 4.0          # 같은 호스트 요청 간격(초)
MAX_EXTRA_PAGES = 5

# (tenant, company) — 2026-10-08 실측으로 존재 확인한 곳만.
TENANTS = [
    ("oci", "OCI"),                 # OCI·OCI Power·OCI정보통신·(인근약품 등 그룹 워크스페이스 포함)
    ("sangmidang", "SPC"),          # 상미당홀딩스(SPC그룹) — 비알코리아 등 계열 공고 포함
]

_last = {}
_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _host(tenant):
    return tenant if "." in tenant else f"{tenant}.career.greetinghr.com"


def _get(url):
    host = url.split("/")[2]
    wait = DELAY - (time.time() - _last.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ko-KR,ko;q=0.9", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # HTTPError 는 그대로 올라감
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
    finally:
        _last[host] = time.time()
    return raw.decode("utf-8", "replace")


def _queries(html):
    m = _NEXT_RE.search(html)
    if not m:
        raise RuntimeError("greetinghr: __NEXT_DATA__ 없음(화면 구조 변경?)")
    data = json.loads(m.group(1))
    return (data.get("props", {}).get("pageProps", {})
            .get("dehydratedState", {}).get("queries", [])) or []


def _openings(queries):
    for q in queries:
        if q.get("queryKey") == ["openings"]:
            return q.get("state", {}).get("data") or []
    return None


def _kst(s):
    if not s:
        return None
    try:
        t = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return t.astimezone(_dt.timezone(_dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return s


_CAREER = {"NEW_COMER": "신입", "EXPERIENCED": "경력", "IRRELEVANT": "경력무관",
           "NEW_COMER_OR_EXPERIENCED": "신입/경력"}
_EMP = {"FULL_TIME_WORKER": "정규직", "CONTRACT_WORKER": "계약직", "INTERN": "인턴",
        "PART_TIME_WORKER": "파트타임", "DISPATCH_WORKER": "파견직", "FREELANCER": "프리랜서"}


def _block(o):
    parts = []
    open_, due = _kst(o.get("openDate")), _kst(o.get("dueDate"))
    parts.append(f"접수 {open_ or '?'} ~ {due or '상시/채용시 마감'}")
    div = (o.get("workspaceDivision") or {}).get("division")
    if div:
        parts.append(f"부문 {div}")
    careers, places, jobs, emps = [], [], [], []
    for p in ((o.get("openingJobPosition") or {}).get("openingJobPositions") or []):
        c = p.get("jobPositionCareer") or {}
        ct = c.get("careerType")
        if ct:
            label = _CAREER.get(ct, ct)
            lo, hi = c.get("careerFrom"), c.get("careerTo")
            if lo is not None or hi is not None:
                label += f"({lo if lo is not None else ''}~{hi if hi is not None else ''}년)"
            careers.append(label)
        for key, field, out in (("workspacePlace", "place", places),
                                ("workspaceJob", "job", jobs),
                                ("workspaceOccupation", "occupation", jobs),
                                ("workspaceField", "field", jobs)):
            v = (p.get(key) or {}).get(field)
            if v:
                out.append(v)
        e = (p.get("jobPositionEmployment") or {}).get("employmentType")
        if e:
            emps.append(_EMP.get(e, e))
    uniq = lambda xs: list(dict.fromkeys(xs))
    if careers:
        parts.append("경력구분 " + "·".join(uniq(careers)))
    if emps:
        parts.append("고용 " + "·".join(uniq(emps)))
    if places:
        parts.append("근무지 " + "·".join(uniq(places)))
    if jobs:
        parts.append("직무 " + "·".join(uniq(jobs)))
    return " | ".join(parts)


def fetch(tenant: str, company: str) -> list:
    """tenant 의 게시 중 공고 전체. tenant 는 'oci' 또는 전체 호스트명."""
    base = f"https://{_host(tenant)}"
    html = _get(base + "/ko/home")
    queries = _queries(html)
    items = _openings(queries)
    if items is None:
        # 홈에 공고 영역이 없으면 디자인 JSON 의 내부 페이지 경로를 따라가 본다.
        blob = json.dumps(queries, ensure_ascii=False)
        paths = [p for p in dict.fromkeys(re.findall(r'"path":\s*"([A-Za-z0-9_-]+)"', blob))
                 if p != "home"][:MAX_EXTRA_PAGES]
        for p in paths:
            items = _openings(_queries(_get(f"{base}/ko/{p}")))
            if items is not None:
                break
    if items is None:
        raise RuntimeError(f"greetinghr {tenant}: 공고 목록(openings)을 찾지 못함")
    out = []
    for o in items:
        if o.get("deploy") is False:
            continue
        out.append({
            "company": company,
            "title": (o.get("title") or "").strip(),
            "href": f"{base}/ko/o/{o.get('openingId')}",
            "block": _block(o),
        })
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
        except Exception as e:  # noqa: BLE001 — 진단 출력용
            print(f"[ERR] {c} ({t}): {type(e).__name__}: {e}")
            continue
        m = [r for r in rows if EHS.search(r["title"] + " " + r["block"])]
        total += len(rows)
        hits += len(m)
        print(f"{c} ({t}): {len(rows)}건, EHS 일치 {len(m)}건")
        for r in m:
            print(f"   - {r['title']} | {r['block']} | {r['href']}")
    print(f"합계 {total}건, EHS 일치 {hits}건")
