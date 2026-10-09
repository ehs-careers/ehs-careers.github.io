# -*- coding: utf-8 -*-
"""공고 정보 검증: 공고 원문을 열어 공식 값으로 기간·근무지·경력·마감 여부를 확인하고, 본문에서 환경·안전 직무 근거를 찾는다.

원문 확인
- 사람인: 링크에 rec_idx 가 있으면 그 공고(og 메타 + view-ajax 상세)
- 잡코리아·인크루트 등: 페이지의 표준 채용공고 데이터(JSON-LD JobPosting) + 본문(잡코리아는 상세 iframe 도 읽음)
- 캐치·인디드·링커리어처럼 열 수 없거나 막힌 곳, 검색 결과 링크: 회사명+공고명으로 사람인을 검색해 같은 공고를 찾는다
직무 확인 (duty_check)
- 본문의 모집분야·담당업무·주요업무 구간에서 환경·안전 직무어를 찾아 근거 문장을 남긴다
- 결과: "확인"(본문 근거 있음) / "제목만"(본문이 이미지 등이라 근거 없음, 제목은 환경·안전) / "의심"(제목·본문 모두 약함)
- 건설현장·시설관리 신호가 보이면 따로 표시(제외 판단은 사람이 하도록 표시만)

사용 (단독):  python verify_jobs.py <입력.json> <출력.json>
"""
import difflib
import gzip
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
GAP = 4.0
SARAMIN = "https://www.saramin.co.kr"


def _get(url, timeout=40, data=None, referer=None):
    from job_radar import http_get, UA
    if data is None:
        return http_get(url, timeout)
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers={
        "User-Agent": UA, "Accept-Encoding": "gzip", "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "Referer": referer or SARAMIN})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
        if r.headers.get("Content-Encoding", "").lower() == "gzip":
            b = gzip.decompress(b)
        return b.decode("utf-8", "replace")


def _retry(fn, *a, tries=3, **k):
    last = None
    for i in range(tries):
        try:
            return fn(*a, **k)
        except Exception as e:  # 사이트가 가끔 전송 중 끊는다
            last = e
            if "HTTP 404" in str(e) or "HTTP 410" in str(e):
                break
            time.sleep((8, 20, 40)[i])
    raise last


def _text(h):
    h = re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S)
    h = re.sub(r"<br\s*/?>|</p>|</li>|</div>|</tr>", "\n", h, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", h))
    return re.sub(r"[ \t\r\f\v ]+", " ", t).strip()


def _flat(t):
    return re.sub(r"\s+", " ", t)


def norm_co(s):
    return re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\s|[()·.,]", "", s or "").lower()


def norm_ti(s):
    s = re.sub(r"\[[^\]]*\]|\([^)]*마감[^)]*\)|D-\d+", " ", s or "")
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def same_month(a, b):
    """제목에 'N월' 이 둘 다 있으면 같아야 같은 공고 (7월 공고 ↔ 8월 공고 오인 방지)"""
    ma, mb = re.findall(r"(\d{1,2})\s*월", a or ""), re.findall(r"(\d{1,2})\s*월", b or "")
    return not (ma and mb) or bool(set(ma) & set(mb))


def rec_idx_of(link):
    m = re.search(r"rec_idx=(\d+)", link or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------- 경력 값
# '학력' 바로 앞의 경력 값만 (머리글의 '경력 채용' 등 제외). 값 형태를 나열해 '신입·경력', '경력무관' 도 잡는다
EXP_RE = re.compile(r"경력\s+(신입\s*[·/,]\s*경력(?:\s*\d+\s*년\s*[↑이상]*)?|경력\s*무관|무관|신입|경력\s*\d+\s*~\s*\d+\s*년|경력\s*\d+\s*년\s*[↑이상]*|\d+\s*~\s*\d+\s*년|\d+\s*년\s*[↑이상]*|경력)\s+학력")


def exp_of(t):
    ms = list(EXP_RE.finditer(t))
    if not ms:
        return None
    v = re.sub(r"\s+", " ", ms[-1].group(1)).strip()
    return v if re.match(r"신입|경력|무관", v) else "경력 " + v


# ---------------------------------------------------------------- 직무 확인
DUTY = re.compile(r"환경\s*[/·&.,]?\s*안전|안전\s*[/·&.,]?\s*환경|E\.?H\.?S|H\.?S\.?E|\bSHE\b|\bESH\b|HSSE|환경\s*(?:관리|담당|엔지니어|기술인|시설|인허가|보건|팀|부)|"
                  r"안전\s*(?:관리|보건|담당|팀|부)|보건\s*관리|산업\s*안전|공정\s*안전|PSM|화학\s*물질|화관법|화평법|유해\s*화학|위험물|대기\s*(?:환경|오염|배출)|수질|폐수|폐기물|"
                  r"온실가스|토양|소방|방재|중대재해|위험성\s*평가|작업\s*환경\s*측정|Safety|Environment(?:al)?", re.I)
SECTION = re.compile(r"(모집\s*분야|모집\s*부문|모집\s*직무|담당\s*업무|주요\s*업무|업무\s*내용|수행\s*업무|직무\s*내용|채용\s*분야|모집\s*요강|Job\s*Description|Responsibilities)", re.I)
EXCL = re.compile(r"건설\s*현장|공사\s*현장|시공|현장\s*대리인|아파트|공동주택|분양|건축\s*현장|플랜트\s*현장|소방\s*시설\s*(?:관리|점검)|시설\s*관리\s*(?:직|원)|경비|미화|"
                  r"측정\s*분석|분석\s*요원|시료\s*채취|영업|세일즈|컨설팅|컨설턴트")


CONSTR = re.compile(r"건설\s*현장|공사\s*현장|시공|현장\s*대리인|아파트|공동주택|분양|건축\s*현장|플랜트\s*현장|PJT\s*직|데이터\s*센터\s*(?:공사|현장|신축)")
BOILER = re.compile(r"채용정보|인크루트|잡코리아|사람인|채용\s*-\s|로그인|회원가입|스크랩|관심기업|추천\s*공고|AI\s*추천|공유하기|신고하기")


def duty_check(title, body):
    """(판정, 근거 문장, 제외 신호) — 판정: 확인 / 제목만 / 의심.
    근거는 본문에서만 찾는다(공고 제목·사이트 머리글 줄은 뺌). 환경·안전 분야 앞에 '(마감)'이 붙어 있으면 따로 알린다."""
    # 제목 판정: '환경(E) 담당', 'ESG 환경', 포털 직무 탭 이름이 그냥 '환경'·'안전'인 경우도 환경·안전 직무로 본다
    title_hit = bool(DUTY.search(title or "") or re.search(r"환경\s*\(\s*E\s*\)|ESG\s*환경|^\s*(?:환경|안전|보건|환경안전|안전환경)\s*$|"
                                                         r"\(\s*(?:[^)]*[·/,]\s*)?(?:환경|안전|보건)(?:\s*[·/,][^)]*)?\s*\)", title or ""))  # '(안전)', '(환경·수질)' 같은 괄호 표기
    body = body or ""
    if title:
        body = body.replace(title, " ")
        body = body.replace(re.sub(r"\s+", " ", title), " ")
    # 사이트 문구가 든 짧은 '문장'만 뺀다 (한 줄로 이어진 본문의 나머지는 유지)
    parts = re.split(r"(\n|(?<=[.。!?])\s)", body)
    body = "".join(p for p in parts if not (len(p) < 120 and BOILER.search(p)))
    windows = []
    for m in SECTION.finditer(body):
        windows.append(body[m.start(): m.start() + 400])
    scope = "\n".join(windows) if windows else body[:6000]
    ev, hit = "", None
    for line in re.split(r"[\n]|(?<=[.。])\s", scope):
        if len(line.strip()) <= 2:
            continue
        for h in DUTY.finditer(line):  # '(마감)화학물질관리' 처럼 마감된 분야는 건너뛰고 다음 직무어를 본다
            if re.search(r"\(\s*마감\s*\)\s*\S{0,8}$", line[:h.start()]):
                continue
            hit = h
            s = max(0, h.start() - 40)
            ev = _flat(line[s: h.end() + 60]).strip(" -·•▶■□○◦*:,")
            break
        if hit:
            break
    # 제외 신호: 건설현장류는 직무 구간 전체+제목에서(결정적), 영업·측정분석 등은 근거 문장 주변+제목에서만
    # (본문 다른 곳의 '영업' 글자로 정상 EHS 공고에 경고가 붙던 문제)
    near = (ev or "") + " " + (title or "")
    found = [m.group(0) for m in CONSTR.finditer(_flat(scope) + " " + (title or ""))] + [m.group(0) for m in EXCL.finditer(near) if not CONSTR.search(m.group(0))]
    ex_s = ", ".join(dict.fromkeys(re.sub(r"\s+", "", x) for x in found))[:60]
    constr_safety = bool(found) and any(CONSTR.search(x) for x in found) and re.search(r"안전\s*관리", near) and not re.search(r"환경", near)
    # 여러 부문 공고에서 환경·안전 분야만 '(마감)' 인 경우
    fl = _flat(scope)
    closed_parts = [m.group(0) for m in re.finditer(r"\(\s*마감\s*\)\s*[^,(\n]{0,20}", fl) if DUTY.search(m.group(0))]
    if closed_parts:
        ex_s = ("환경·안전 분야 마감: " + ", ".join(dict.fromkeys(p.strip() for p in closed_parts))[:60] + (" · " + ex_s if ex_s else "")).strip(" ·")
    if constr_safety:
        verdict = "의심"  # 건설현장 안전관리자 유형 (사용자 기준 제외 대상)
    elif hit:
        verdict = "확인"
    elif title_hit:
        verdict = "제목만"
    else:
        verdict = "의심"
    return verdict, ev[:140], ex_s


# ---------------------------------------------------------------- 사이트별 원문
def saramin_detail(rec):
    url = f"{SARAMIN}/zf_user/jobs/relay/view?view_type=list&rec_idx={rec}"
    page = _retry(_get, url)
    out = {"rec_idx": rec, "link": url, "source_site": "사람인"}
    m = re.search(r"company-info/view\?csn=([A-Za-z0-9%=+/]+)", page)  # 회사 비교(company_info)용 사람인 회사 번호
    if m:
        out["csn"] = urllib.parse.unquote(m.group(1))
    m = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', page)
    if m:
        parts = [p.strip() for p in html.unescape(m.group(1)).split(",")]
        if parts:
            out["company_full"] = parts[0]
        if len(parts) > 1:
            out["title_full"] = parts[1]
        for p in parts:
            if p.startswith("마감일:"):
                out["deadline"] = p[4:].strip()
    time.sleep(GAP)
    aj = _retry(_get, f"{SARAMIN}/zf_user/jobs/relay/view-ajax", data={"rec_idx": rec, "rec_seq": "0", "view_type": "list"}, referer=url)
    body = _text(aj)
    t = _flat(body)

    def grab(label, stop):
        m = re.search(label + r"\s+(.+?)\s+(?:" + stop + ")", t)
        return m.group(1).strip() if m else ""

    m = re.search(r"시작일\s+(\d{4})\.(\d{2})\.(\d{2})(?:\s+(\d{2}:\d{2}))?", t)
    if m:
        out["start"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"마감일\s+(\d{4})\.(\d{2})\.(\d{2})(?:\s+(\d{2}:\d{2}))?", t)
    if m:
        out["deadline"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        out["deadline_time"] = m.group(4) or ""
    elif re.search(r"마감일\s+(?:상시채용|채용시)", t):
        out["deadline"] = "상시"
    e = exp_of(t)
    if e:
        out["exp"] = e
    out["edu"] = grab("학력", "근무형태|급여|자격요건")
    out["emp_type"] = re.split(r"\s*근무형태\s*상세|\s*상세\s*보기|\s*닫기", grab("근무형태", "급여|자격요건|근무지역|직급"))[0].strip()
    out["location"] = re.sub(r"\s*(?:지도|최저임금계산).*$", "", grab("근무지역", "최저임금계산|지도|조회수|홈페이지|급여"))[:60]
    out["closed"] = bool(re.search(r"접수\s*마감된\s*공고|마감된\s*공고입니다|채용\s*마감", t)) and "남은 기간" not in t
    out["_body"] = body
    return {k: v for k, v in out.items() if v not in ("", None)}


def _jsonld_posting(page):
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            d = json.loads(m.group(1).strip())
        except Exception:
            continue
        for x in (d if isinstance(d, list) else d.get("@graph", [d]) if isinstance(d, dict) else []):
            if isinstance(x, dict) and x.get("@type") == "JobPosting":
                return x
    return None


EMP = {"FULL_TIME": "정규직", "PART_TIME": "파트타임", "CONTRACTOR": "계약직", "TEMPORARY": "계약직", "INTERN": "인턴", "OTHER": ""}


def jsonld_detail(url):
    page = _retry(_get, url)
    jp = _jsonld_posting(page)
    if not jp:
        raise RuntimeError("표준 채용공고 데이터 없음")
    host = urllib.parse.urlparse(url).hostname or ""
    out = {"link": url, "source_site": {"www.jobkorea.co.kr": "잡코리아", "job.incruit.com": "인크루트"}.get(host, host)}
    out["title_full"] = html.unescape(jp.get("title", ""))
    out["company_full"] = html.unescape((jp.get("hiringOrganization") or {}).get("name", ""))
    if jp.get("datePosted"):
        out["start"] = jp["datePosted"][:10]
    vt = jp.get("validThrough") or ""
    if re.match(r"\d{4}-\d{2}-\d{2}", vt):
        out["deadline"] = vt[:10]
        if re.match(r".{10}T\d{2}:\d{2}", vt):
            out["deadline_time"] = vt[11:16]
    et = jp.get("employmentType")
    et = et if isinstance(et, list) else [et] if et else []
    t = _flat(_text(page))
    ogd = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', page)
    mi = re.search(r"모집\s*요강|모집\s*분야|모집\s*부문", t)
    body = "\n".join(filter(None, [html.unescape(ogd.group(1)) if ogd else "", t[mi.start(): mi.start() + 1500] if mi else ""]))
    m = re.search(r"고용\s*형태\s*[:：]?\s*([가-힣A-Za-z0-9 ,()·/]+?)\s+(?:급여|근무시간|근무지|모집인원|직급|학력|$)", t)
    out["emp_type"] = (m.group(1).strip() if m else "") or ", ".join(filter(None, (EMP.get(x, "") for x in et)))
    m = re.search(r"경력\s*[:：]\s*([^,|]+?)\s*(?:,|\||학력)", t) or re.search(r"경력\s*[:：]\s*([^,|<]{2,20})", html.unescape(page))
    exr = jp.get("experienceRequirements")
    if m:
        out["exp"] = re.sub(r"\s+", " ", m.group(1)).strip()
    elif isinstance(exr, dict) and exr.get("monthsOfExperience"):
        out["exp"] = f"경력 {int(exr['monthsOfExperience']) // 12}년↑"
    elif isinstance(exr, str):
        out["exp"] = exr
    edu = jp.get("educationRequirements")
    out["edu"] = edu if isinstance(edu, str) else (edu or {}).get("credentialCategory", "") if isinstance(edu, dict) else ""
    addr = ((jp.get("jobLocation") or {}) if isinstance(jp.get("jobLocation"), dict) else (jp.get("jobLocation") or [{}])[0]).get("address", {})
    if isinstance(addr, dict):
        out["location"] = (" ".join(filter(None, [addr.get("addressRegion"), addr.get("addressLocality")])) or addr.get("streetAddress", "")).strip()[:60]
    # 잡코리아: 상세 본문은 별도 iframe
    if "jobkorea.co.kr" in host:
        g = re.search(r"GI_Read/(\d+)", url)
        if g:
            time.sleep(GAP)
            try:
                body += "\n" + _text(_retry(_get, f"https://www.jobkorea.co.kr/Recruit/GI_Read_Comt_Ifrm?Gno={g.group(1)}"))
            except Exception:
                pass
    desc = _text(jp.get("description", ""))
    out["_body"] = body + "\n" + desc
    today = datetime.now(KST).strftime("%Y-%m-%d")
    out["closed"] = bool(out.get("deadline") and out["deadline"] < today) or bool(re.search(r"접수\s*마감|마감된\s*공고|채용이\s*마감", t[:3000]))
    return {k: v for k, v in out.items() if v not in ("", None)}


def saramin_find(company, title):
    """회사명+공고명으로 사람인을 검색해 가장 비슷한 공고 번호를 찾는다. (rec_idx, 점수) 또는 (None, 점수)"""
    nco, nti = norm_co(company), norm_ti(title)
    words = re.sub(r"\[[^\]]*\]|\([^)]*\)|—.*$", " ", title or "")
    co = re.sub(r"\(주\)|㈜|주식회사|→.*$", "", company or "").strip()
    q = f"{co} {' '.join(words.split()[:4])}"
    page = _retry(_get, f"{SARAMIN}/zf_user/search/recruit?searchword={urllib.parse.quote(q)}&recruitPageCount=40")
    best, score = None, 0.0
    for m in re.finditer(r'class="item_recruit"\s+value="(\d+)"(.*?)(?=class="item_recruit"|$)', page, re.S):
        rec, chunk = m.group(1), m.group(2)[:6000]
        t = re.search(r'class="job_tit".*?title="([^"]+)"', chunk, re.S)
        c = re.search(r'class="corp_name".*?>\s*([^<]+?)\s*</a>', chunk, re.S)
        if not t:
            continue
        raw_t = html.unescape(t.group(1))
        if not same_month(title, raw_t):
            continue
        ct, cc = norm_ti(raw_t), norm_co(html.unescape(c.group(1)) if c else "")
        co_ok = nco and cc and (nco in cc or cc in nco)
        s = difflib.SequenceMatcher(None, nti, ct).ratio() + (0.35 if co_ok else -0.5)
        if s > score:
            best, score = rec, s
    return (best, round(score, 2)) if score >= 0.95 else (None, round(score, 2))


def saramin_company_find(company, title, sources_path=None):
    """소스 목록에 있는 그 회사의 사람인 회사 페이지에서 같은 공고를 찾는다 (캐치 등에서 온 공고용)."""
    import os
    p = sources_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.json")
    try:
        srcs = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None, 0.0
    nco, nti = norm_co(company), norm_ti(title)
    pages = [s["url"] for s in srcs if "view-inner-recruit" in s.get("url", "") and nco and
             (lambda c: c and (nco in c or c in nco))(norm_co(s.get("company", "")))]
    best, score = None, 0.0
    for u in pages[:3]:
        try:
            page = _retry(_get, u)
        except Exception:
            continue
        for m in re.finditer(r'<a[^>]+rec_idx=(\d+)[^>]*>(.*?)</a>', page, re.S):
            raw_t = _flat(_text(m.group(2)))
            t = norm_ti(raw_t)
            if len(t) < 4 or not same_month(title, raw_t):
                continue
            s = difflib.SequenceMatcher(None, nti, t).ratio()
            if s > score:
                best, score = m.group(1), s
        time.sleep(GAP)
    return (best, round(score, 2)) if score >= 0.85 else (None, round(score, 2))


DIRECT = ("jobkorea.co.kr", "incruit.com")          # 원문을 직접 읽는 사이트
BLOCKED = ("catch.co.kr", "indeed.com", "linkareer.com", "linkedin.com", "wanted.co.kr")  # 막힘·로그인 → 사람인에서 다시 찾기


def detail_for(link, company="", title="", search=True):
    """링크 → (원문 값 dict, 찾은 방법) 또는 (None, 이유)"""
    host = urllib.parse.urlparse(link or "").hostname or ""
    rec = rec_idx_of(link)
    if rec:
        return saramin_detail(rec), "링크"
    if any(h in host for h in DIRECT) and not re.search(r"/Search|searchword|stext=|kw=", link):
        try:
            return jsonld_detail(link), "링크"
        except Exception as e:
            why = str(e)[:60]
        else:
            why = ""
    else:
        why = "직접 열 수 없는 사이트" if any(h in host for h in BLOCKED) else ("검색 결과 링크" if host else "링크 없음")
    if search and company and title:
        rec, sc = saramin_find(company, title)
        time.sleep(GAP)
        if rec:
            return saramin_detail(rec), f"사람인에서 같은 공고 찾음(일치도 {sc})"
        rec, sc2 = saramin_company_find(company, title)
        if rec:
            return saramin_detail(rec), f"사람인 회사 페이지에서 같은 공고 찾음(일치도 {sc2})"
        why += f" · 사람인에서 같은 공고 못 찾음"
    return None, why


def verify(job):
    """job dict → 검증 결과 dict. verify·duty 문구는 화면에 그대로 보인다."""
    now = datetime.now(KST).strftime("%Y-%m-%d")
    d, how = detail_for(job.get("link", ""), job.get("company", ""), job.get("title", ""))
    if not d:
        v, ev, ex = duty_check(job.get("title", ""), "")
        return {"verify": f"원문 확인 필요 ({how})", "verified_at": now, "duty": v, "duty_evidence": ev, "duty_flag": ex}
    # 수집된 제목과 원문 공식 제목을 함께 본다 (수집 제목에만 '(안전관리자·환경관리)' 같은 분야가 붙은 경우)
    v, ev, ex = duty_check(" ".join(dict.fromkeys(filter(None, [job.get("title", ""), d.get("title_full", "")]))), d.pop("_body", ""))
    d.update(duty=v, duty_evidence=ev, duty_flag=ex, verified_at=now,
             verify=("마감됨 · " if d.get("closed") else "") + f"{d.get('source_site', '')} 원문 확인 {now[5:].replace('-', '.')} ({how})")
    return d


def main():
    src, dst = sys.argv[1], sys.argv[2]
    jobs = json.load(open(src, encoding="utf-8"))
    out = []
    for i, j in enumerate(jobs, 1):
        try:
            r = verify(j)
        except Exception as e:
            r = {"verify": "확인 실패: " + str(e)[:80]}
        out.append({**j, "verified": r})
        print(f"[{i}/{len(jobs)}] {j.get('company')} | {j.get('title', '')[:28]} → {r.get('verify')} | 직무 {r.get('duty')} {('· ' + r['duty_flag']) if r.get('duty_flag') else ''} | {r.get('duty_evidence', '')[:50]}", flush=True)
        json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(GAP)


if __name__ == "__main__":
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    main()
