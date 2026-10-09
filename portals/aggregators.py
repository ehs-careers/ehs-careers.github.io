"""포털 '한 그물' 수집기 — 기업 규모 필터로 대기업·중견 공고만 한 번에 받는다 (표준 라이브러리만).

왜: 키워드 검색 전체 결과에서는 대기업 공고가 소기업 공고에 묻힌다(CLAUDE.md 교훈 1).
포털 자체의 '기업형태' 필터를 쓰면 회사별 페이지 수백 곳을 돌지 않고도 대기업 공고를 한 번에 본다.

확인된 주소 (2026-10-08 실측, 모두 로그인 없이 정적 응답):
- 사람인  /zf_user/search/recruit?searchword=<kw>&company_type=scale001,scale002,public,foreign
          &recruitSort=reg_dt&recruitPageCount=100&recruitPage=<n>
          company_type: scale001 대기업 · scale002 매출1000대기업 · scale003 중견 · public 공사·공기업 · foreign 외국계
          결과는 HTML(div.item_recruit) 안에 그대로 있음. 100건/쪽, gzip 필수(2.7MB → 압축 전송).
- 잡코리아 /Search/?stext=<kw>&tabType=recruit&cotype=1,2,3,6,8&Page_No=<n>
          cotype: 1 대기업 · 2 30대그룹사 · 3 매출1000대 · 4 중견 · 6 외국계 · 8 공공기관·공기업
          Next.js RSC 스트림(self.__next_f.push) 안의 JSON {"pageSize":20,...,"content":[...]}. 20건/쪽 고정.
- 자소설닷컴 /api/v1/employment_companies?per_page=100&page=<n>&after_end_time=<YYYY-MM-DD>
          [&by_business_types=big_business,middle_market,public_institution]
          공개 JSON. 응답 헤더 'total-page' 는 실제로는 전체 건수. keyword 파라미터는 이 엔드포인트에서 무시됨
          → 진행 중 공고 전체(약 300건)를 받아 직무(field)로 직접 거른다.

규칙: robots.txt 확인(허용 경로만), 같은 호스트 요청 간 4.5초, 사이트당 50회 미만, 로그인·봇 확인 우회 없음.
"""
from __future__ import annotations

import gzip
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
TIMEOUT = 20
GAP = 4.5                      # 같은 호스트 요청 간격(초)
MAX_REQ_PER_HOST = 240         # 사이트당 상한 — 2026-10-09 누락 점검 후 검색어·깊이 확대(요청 간 4.5초 유지, 사람인 ≈ 12분)
KST = timezone(timedelta(hours=9))

# 재현율 우선의 넓은 직무 그물 (최종 판정은 ml/relevance.py 가 한다)
EHS_RE = re.compile(r"환경|안전|보건|산업위생|공정안전|PSM|화학물질|화관법|위험물|EHS|HSE|ESH|SHE|HSEQ|온실가스|대기|수질|폐기물", re.I)

# 2026-10-09 누락 점검: '안전보건'·'중대재해'로만 잡히는 대기업 공고(한진·BGF리테일·KREAM)가 있어 확대
KEYWORDS = ["환경안전", "EHS", "안전관리", "공정안전", "화학물질", "보건관리자", "안전보건", "중대재해", "산업안전", "안전환경", "SHE", "HSE", "PSM", "환경관리"]

_last: dict[str, float] = {}
_count: dict[str, int] = {}
_robots: dict[str, list[str]] = {}


class Budget(Exception):
    pass


def _get(url: str, accept: str = "text/html,application/xhtml+xml") -> tuple[str, dict]:
    host = urllib.parse.urlsplit(url).netloc
    if _count.get(host, 0) >= MAX_REQ_PER_HOST:
        raise Budget(f"{host}: 요청 상한 {MAX_REQ_PER_HOST}회 도달")
    wait = _last.get(host, 0) + GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept,
                                               "Accept-Language": "ko-KR,ko;q=0.9", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
            if r.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
            return body.decode("utf-8", "replace"), dict(r.headers.items())
    finally:
        _last[host] = time.monotonic()
        _count[host] = _count.get(host, 0) + 1


def _allowed(url: str) -> bool:
    """robots.txt 의 'User-agent: *' 구역 Disallow 를 따른다(와일드카드 * 지원)."""
    p = urllib.parse.urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rules: list[str] = []
        try:
            txt, _ = _get(base + "/robots.txt", "text/plain")
            star = False
            for line in txt.splitlines():
                line = line.split("#", 1)[0].strip()
                if ":" not in line:
                    continue
                k, v = (s.strip() for s in line.split(":", 1))
                k = k.lower()
                if k == "user-agent":
                    star = v == "*"
                elif star and k == "disallow" and v:
                    rules.append(v)
        except Exception:
            pass
        _robots[base] = rules
    path = p.path + ("?" + p.query if p.query else "")
    for rule in _robots[base]:
        rx = "^" + ".*".join(re.escape(x) for x in rule.split("*"))
        if re.match(rx, path):
            return False
    return True


def _txt(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


# ---------------------------------------------------------------- 사람인
SARAMIN = "https://www.saramin.co.kr"
SARAMIN_LARGE = "scale001,scale002,public,foreign"   # 대기업·매출1000대·공기업·외국계
SARAMIN_MID = "scale003"                            # 중견
_SR_SIZE = {"scale001": "대기업", "scale002": "매출1000대", "scale003": "중견", "public": "공기업", "foreign": "외국계"}


def fetch_saramin(keyword: str, company_type: str = SARAMIN_LARGE, max_pages: int = 2, sort: str = "reg_dt") -> list[dict]:
    """사람인 검색 + 기업형태 필터. sort=reg_dt(등록일순)라 매일 돌리면 새 공고가 앞쪽 쪽에 온다."""
    out, seen = [], set()
    size_label = "/".join(_SR_SIZE.get(c, c) for c in company_type.split(",")) if company_type else "전체"
    for page in range(1, max_pages + 1):
        params = {"searchword": keyword, "recruitSort": sort, "recruitPageCount": 100, "recruitPage": page}
        if company_type:
            params["company_type"] = company_type
        q = urllib.parse.urlencode(params, safe=",")
        url = f"{SARAMIN}/zf_user/search/recruit?{q}"
        if not _allowed(url):
            break
        html, _ = _get(url)
        m = re.search(r'cnt_result">\s*총\s*([\d,]+)건', html)
        total = int(m.group(1).replace(",", "")) if m else 0
        chunks = re.split(r'<div class="item_recruit"', html)[1:]
        for ch in chunks:
            rec = re.search(r'value="(\d+)"', ch)
            if not rec or rec.group(1) in seen:
                continue
            seen.add(rec.group(1))
            t = re.search(r'<h2 class="job_tit">\s*<a[^>]*title="([^"]*)"', ch)
            corp = re.search(r'class="corp_name">\s*<a([^>]*)>\s*(.*?)\s*</a>', ch, re.S)
            csn = re.search(r"csn=([^\"&]+)", corp.group(1)) if corp else None
            date = re.search(r'<span class="date">(.*?)</span>', ch, re.S)
            cond = re.search(r'<div class="job_condition">(.*?)</div>', ch, re.S)
            sector = re.search(r'<div class="job_sector">(.*?)</div>', ch, re.S)
            conds = [_txt(x) for x in re.findall(r"<span>(.*?)</span>", cond.group(1), re.S)] if cond else []
            sec = _txt(sector.group(1)) if sector else ""
            block = " · ".join(x for x in [
                _txt(date.group(1)) if date else "", *conds, sec, f"기업형태:{size_label}",
                f"검색:{keyword}", f"사람인 회사키:{csn.group(1)}" if csn else ""] if x)
            out.append({"company": _txt(corp.group(2)) if corp else "",
                        "title": unescape(t.group(1)) if t else "",
                        "href": f"{SARAMIN}/zf_user/jobs/relay/view?view_type=search&rec_idx={rec.group(1)}",
                        "block": block})
        if len(chunks) < 100 or page * 100 >= total:
            break
    return out


def fetch_saramin_large(keyword: str) -> list[dict]:
    return fetch_saramin(keyword, SARAMIN_LARGE, max_pages=5)   # 최근 500건: 수집 시작 전 공고도 (10/4 이전 등록분 누락 사례)


def fetch_saramin_all(keyword: str) -> list[dict]:
    """기업형태 필터 없이 — 작은 대기업 계열사(스틸싸이클·한솔티씨에스 등)는 '대기업' 필터에 안 걸린다. 공정위 명단 대조는 수집기가(groups_only)"""
    return fetch_saramin(keyword, "", max_pages=3)


def fetch_saramin_mid(keyword: str) -> list[dict]:
    return fetch_saramin(keyword, SARAMIN_MID, max_pages=2)


# ---------------------------------------------------------------- 잡코리아
JOBKOREA = "https://www.jobkorea.co.kr"
JK_LARGE = "1,2,3,6,8"   # 대기업·30대그룹·매출1000대·외국계·공공
JK_MID = "4"
_JK_CAREER = {"1": "신입", "2": "경력", "3": "신입·경력", "4": "경력무관"}


def _jk_flight(html: str) -> str:
    parts = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html)
    return "".join(json.loads('"' + p + '"') for p in parts)


def fetch_jobkorea(keyword: str, cotype: str = JK_LARGE, max_pages: int = 3) -> list[dict]:
    """잡코리아 검색 + 기업형태(cotype) 필터. 20건/쪽 고정, 관련도순."""
    out, seen = [], set()
    dec = json.JSONDecoder()
    for page in range(1, max_pages + 1):
        q = urllib.parse.urlencode({"stext": keyword, "tabType": "recruit", "cotype": cotype, "Page_No": page}, safe=",")
        url = f"{JOBKOREA}/Search/?{q}"
        if not _allowed(url):
            break
        flight = _jk_flight(_get(url)[0])
        data = None
        for m in re.finditer(r'\{"pageSize":', flight):
            try:
                obj, _ = dec.raw_decode(flight, m.start())
            except ValueError:
                continue
            c = obj.get("content") or []
            if c and isinstance(c[0], dict) and "legacyJobNo" in c[0]:   # 정규 공고 목록(알바·기업 목록 제외)
                data = obj
                break
        if not data:
            break
        for c in data["content"]:
            jid = str(c.get("id"))
            if jid in seen:
                continue
            seen.add(jid)
            per = c.get("applicationPeriod") or {}
            end = (per.get("end") or "")[:16].replace("T", " ")
            end = "상시" if end.startswith("2070") else end
            block = " · ".join(x for x in [
                f"{(per.get('start') or '')[:10]} ~ {end}",
                _JK_CAREER.get(str(c.get("careerType")), ""),
                (c.get("jobClassificationOrIndustry") or "").strip(","),
                f"그룹:{c['companySubsidiary']}" if c.get("companySubsidiary") else "",
                f"매출순위:{c['companyRank']}" if c.get("companyRank") else "",
                f"기업형태:{'중견' if cotype == JK_MID else '대기업/공공/외국계'}", f"검색:{keyword}"] if x)
            out.append({"company": c.get("postingCompanyName") or c.get("companyName") or "",
                        "title": c.get("title") or "",
                        "href": f"{JOBKOREA}/Recruit/GI_Read/{jid}",
                        "block": block})
        if (data.get("pageNumber", 0) + 1) >= data.get("totalPages", 0):
            break
    return out


def fetch_jobkorea_large(keyword: str) -> list[dict]:
    return fetch_jobkorea(keyword, JK_LARGE, max_pages=4)


# ---------------------------------------------------------------- 자소설닷컴
JASOSEOL = "https://jasoseol.com"
_JS_DIV = {1: "신입", 2: "경력", 3: "인턴", 4: "계약직", 7: "교육"}
_JS_SIZE = {"big_business": "대기업", "middle_market": "중견", "public_institution": "공공기관", "other_business_type": "기타"}


def fetch_jasoseol(business_types: str | None = None, ehs_only: bool = True, max_pages: int = 6) -> list[dict]:
    """자소설닷컴 진행 중 공고 전체(공개 JSON). 공고 1건에 직무(field) 여러 개 → EHS 직무만 펼쳐서 반환."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    out = []
    for page in range(1, max_pages + 1):
        params = {"per_page": 100, "page": page, "after_end_time": today}
        if business_types:
            params["by_business_types"] = business_types
        url = f"{JASOSEOL}/api/v1/employment_companies?" + urllib.parse.urlencode(params, safe=",")
        if not _allowed(url):
            break
        body, headers = _get(url, "application/json")
        rows = json.loads(body)
        for r in rows:
            cg = r.get("company_group") or {}
            size = _JS_SIZE.get(cg.get("business_size") or "", cg.get("business_size") or "")
            period = f"{(r.get('start_time') or '')[:16].replace('T', ' ')} ~ {(r.get('end_time') or '')[:16].replace('T', ' ')}"
            href = f"{JASOSEOL}/recruit/{r['id']}"
            emps = r.get("employments") or []
            hits = [e for e in emps if EHS_RE.search(e.get("field") or "")]
            title_hit = bool(EHS_RE.search(r.get("title") or ""))
            if ehs_only and not hits and not title_hit:
                continue
            for e in (hits or [None]) if ehs_only else (emps or [None]):
                div = "/".join(_JS_DIV.get(d, str(d)) for d in (e or {}).get("division") or [])
                out.append({"company": r.get("name") or "",
                            "title": f"{r.get('title')} | {e.get('field')}" if e else (r.get("title") or ""),
                            "href": href,
                            "block": " · ".join(x for x in [period, div, f"기업형태:{size}" if size else "",
                                                            f"그룹:{cg.get('name')}" if cg.get("name") else "",
                                                            f"원 채용페이지:{r.get('employment_page_url')}" if r.get("employment_page_url") else ""] if x)})
        total = int(headers.get("total-page") or headers.get("Total-Page") or 0)   # 실제로는 전체 건수
        if len(rows) < 100 or page * 100 >= total:
            break
    return out


# ---------------------------------------------------------------- 묶음
def _dedup(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        if r["href"] + r["title"] not in seen:
            seen.add(r["href"] + r["title"])
            out.append(r)
    return out


def _multi(fn, keywords):
    def run():
        rows = []
        for kw in keywords:
            try:
                rows += fn(kw)
            except Budget:
                break
        return _dedup(rows)
    return run


FETCHERS = {
    # 사람인: 대기업망 6키워드 × 최대 2쪽 = ≤12회, 중견망 6회 → robots 포함 ≤19회
    "사람인 대기업·공기업·외국계": _multi(fetch_saramin_large, KEYWORDS),
    "사람인 중견": _multi(fetch_saramin_mid, KEYWORDS),
    "사람인 전체(대기업 계열사 대조)": _multi(fetch_saramin_all, KEYWORDS),
    # 잡코리아: 6키워드 × 최대 3쪽 = ≤18회
    "잡코리아 대기업·공기업·외국계": _multi(fetch_jobkorea_large, KEYWORDS),
    # 자소설닷컴: 진행 중 전체 ≈ 3~4회 (키워드 불필요)
    "자소설닷컴": lambda: fetch_jasoseol(),
}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    names = sys.argv[1:] or list(FETCHERS)
    for name in names:
        t0 = time.time()
        try:
            rows = FETCHERS[name]()
        except Exception as e:
            print(f"[{name}] 실패: {e!r}")
            continue
        ehs = [r for r in rows if EHS_RE.search(r["title"])]
        print(f"\n[{name}] {len(rows)}건 (제목 EHS 일치 {len(ehs)}건) · {time.time() - t0:.0f}초")
        for r in ehs[:12]:
            print(f"  - {r['company']} | {r['title'][:70]}\n    {r['href']}\n    {r['block'][:150]}")
    print("\n요청 수:", _count)
