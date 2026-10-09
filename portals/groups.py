"""대기업 그룹 자체 채용 사이트 공고 목록 — 표준 라이브러리(urllib)만 사용.

각 fetch_*() 는 '지금 접수 중인' 공고 전부를
  {"company", "title", "href"(공고 원문 주소), "block"(구분 · 접수기간 · 근무지 · 분야를 한 줄로)}
목록으로 돌려준다. HTTP 오류(4xx/5xx)는 그대로 올라간다(urllib.error.HTTPError).
규칙: robots.txt 준수 · 같은 호스트 요청 간격 4초 이상 · 사이트당 40회 미만 · 봇 확인/로그인 우회 없음.

엔드포인트 (2026-10-08 이 PC 에서 실측)
  현대자동차  GET  talent.hyundai.com/api/rec/AP-HM-FO-02700?hgrCd=1&lang=ko&page=N&pageblock=100   (JSON)
              원문: /apply/applyView.hc?recuYy=&recuType=&recuCls=
              ※ 첫 화면은 NetFunnel(접속 대기열) 페이지. 목록 API 자체는 대기열 토큰 없이 응답한다.
                API 가 JSON 을 안 주면, 화면이 대기열 통과 후 제출하는 폼(nfGubnC 고정값)과 같은 GET 을
                한 번 보내 세션을 만든 뒤 재시도한다(대기열 서버는 부르지 않음).
  HD현대      GET  recruit.hd.com/api/v1/jobda/getRecruitNoticeList?isPost=true&LANG=KR   (JSON 약 1.3MB, 과거 공고 포함)
              → 접수기간으로 진행 중만 남김. 원문: hd.recruiter.co.kr/career/jobs/<sn>
  롯데        GET  recruit.lotte.co.kr/apply/announcement   (서버 렌더링 HTML, '전체(N)' 전부 한 화면)
  CJ          POST recruit.cj.net/recruit/ko/recruit/recruit/searchNewGonggoList.fo  (JSON 본문, pageIndex=200)
              원문: gubun 1 → detail.fo?zz_jo_num= / gubun 2 → bestDetail.fo?direct=N&zz_jo_num=
  두산        GET  career.doosan.com/dsp/sa/RecList.jsp   (서버 렌더링 HTML)
              원문: 상세는 POST 폼 이동뿐이라, GET 으로 열리는 RecForm.jsp?REC_ID=<id>&viewType=AD 를 씀
  효성        POST hyosung.recruiter.co.kr/appsite/company/getMainView  (appsiteSn=12931&settingType=E)
              (마이다스 구형 appsite. robots: Disallow:/ + Allow:/appsite/ → RFC 9309 최장 일치로 /appsite/ 허용)
              원문: /app/jobnotice/view?systemKindCode=MRS2&jobnoticeSn=<sn>  (링크만, 수집기는 열지 않음)
              ※ 메인 화면용 목록이라 최근 공고 몇 건(관측상 최대 6건)만 옴 — 동시 진행이 많으면 누락 가능.
  LS          위와 같은 appsite 방식: LS MnM(lsmnm) · 가온전선(gaoncable).
              나머지 LS 계열(e1, ls-lnf, lsat, lsitc, lsmtron)은 신형 /career 로 넘어갔고 목록 API 호스트
              robots 가 전체 금지 → 제외(portals/recruiter.py 참고). lsis 는 DNS 없음.
  에코프로    GET  ecoprorecruit.co.kr/eco_pro/api/user/apply/list   (JSON) 원문: /eco_pro/page/user/apply_view?target=<id>
  고려아연    GET  careers.koreazinc.co.kr/v1/admin/recruit/recruit-announce/api/getList?rowsPerPage=50&noticeYn=Y&page=N
              → progressStatus != END 만. 원문: /recruit/announceView?recruitNoticeSn=<sn>
  금호석유화학 POST recruit.kkpc.com/main/recruit/27_4/menu2_6.jsp  (서버 렌더링 HTML, menufg=2&smenufg=6)
              원문: menu2_6view.jsp?drno=<id>

제외 (FETCHERS 에 없음)
  포스코      recruit.posco.com/robots.txt = "User-agent: * / Disallow: /" (네이버 Yeti 만 허용) → 수집 안 함
  GS칼텍스    recruit.gscaltex.com 은 이 PC 에서 TLS 연결이 끊김(크롬도 ERR_CONNECTION_RESET).
              gscaltex.recruiter.co.kr appsite 는 응답하지만 진행 공고 0건이라 현행 채널인지 확인 불가.
  HS효성      hshyosung.recruiter.co.kr → 신형 /career (목록 API robots 전체 금지)
"""
import gzip
import html as _html
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
EHS_FIRST = re.compile(r"안전|보건|환경|EHS|SHE|HSE|PSM", re.I)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
TIMEOUT = 20
GAP = 4.0            # 같은 호스트 요청 간격(초)
MAX_REQ_PER_HOST = 39
KST = timezone(timedelta(hours=9))

_last: dict = {}
_count: dict = {}


class Session:
    """호스트별 간격·횟수 제한 + (선택) 쿠키를 가진 작은 HTTP 도우미."""

    def __init__(self, cookies: bool = False):
        handlers = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())] if cookies else []
        self.opener = urllib.request.build_opener(*handlers)

    def request(self, url, data=None, headers=None, method=None, retries=1) -> str:
        host = urllib.parse.urlsplit(url).netloc
        if _count.get(host, 0) >= MAX_REQ_PER_HOST:
            raise RuntimeError(f"{host}: 요청 상한 {MAX_REQ_PER_HOST}회 초과")
        h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "ko-KR,ko;q=0.9",
             "Accept-Encoding": "gzip, deflate"}
        if headers:
            h.update(headers)
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        elif isinstance(data, str):
            data = data.encode("utf-8")
        for attempt in range(retries + 1):
            wait = GAP - (time.time() - _last.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            _count[host] = _count.get(host, 0) + 1
            req = urllib.request.Request(url, data=data, headers=h, method=method)
            try:
                with self.opener.open(req, timeout=TIMEOUT) as r:   # HTTPError 는 그대로 올라감
                    raw = r.read()
                    enc = (r.headers.get("Content-Encoding") or "").lower()
                    charset = r.headers.get_content_charset() or "utf-8"
                break
            except urllib.error.HTTPError:
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt >= retries:
                    raise
                print(f"[groups] {host} 재시도: {e!r}", file=sys.stderr)
            finally:
                _last[host] = time.time()
        if "gzip" in enc:
            raw = gzip.decompress(raw)
        elif "deflate" in enc:
            raw = zlib.decompress(raw)
        return raw.decode(charset, "replace")

    def json(self, url, **kw):
        kw.setdefault("headers", {}).setdefault("Accept", "application/json, text/plain, */*")
        t = self.request(url, **kw)
        try:
            return json.loads(t)
        except ValueError:
            raise RuntimeError(f"JSON 아님 ({url}): {t[:200]!r}")


def _text(s) -> str:
    s = re.sub(r"<[^>]+>", " ", str(s or ""))
    return re.sub(r"\s+", " ", _html.unescape(s)).strip()


def _join(*parts) -> str:
    return " | ".join(p for p in (_text(x) for x in parts) if p)


def _now_kst() -> datetime:
    return datetime.now(KST)


def _ymd(s) -> str:
    """'20261011' / '2026-10-11 23:59:59' / '2026.10.11' → '2026-10-11'"""
    d = re.sub(r"\D", "", str(s or ""))[:8]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else ""


# ───────────────────────── 현대자동차 ─────────────────────────
HY = "https://talent.hyundai.com"


def fetch_hyundai() -> list:
    s = Session(cookies=True)
    api_h = {"Referer": HY + "/apply/applyList.hc", "X-HKMC-SERVICE": "HM"}

    def page(n):
        q = urllib.parse.urlencode({"hgrCd": "1", "lang": "ko", "page": n, "pageblock": 100})
        return s.json(f"{HY}/api/rec/AP-HM-FO-02700?{q}", headers=dict(api_h))

    try:
        d = page(1)
    except RuntimeError:
        # 세션이 필요할 때: 목록 화면 → (대기열 통과 후 화면이 보내는) nfGubnC 폼 GET → 재시도
        t = s.request(HY + "/apply/applyList.hc")
        m = re.search(r'name="nfGubnC"\s+value="([^"]+)"', t)
        if m:
            s.request(HY + "/apply/applyList.hc?" + urllib.parse.urlencode({"nfGubnC": m.group(1)}))
        d = page(1)
    data = d.get("data") or {}
    items = list(data.get("list") or [])
    total = int(data.get("listCnt") or 0)
    n = 1
    while len(items) < total and n < 10:
        n += 1
        more = (page(n).get("data") or {}).get("list") or []
        if not more:
            break
        items.extend(more)
    out = []
    for o in items:
        if o.get("groupYn") == "Y" and o.get("ntcGroupNo"):
            href = f"{HY}/apply/applyGroupView.hc?ntcGroupNo={o['ntcGroupNo']}"
        else:
            href = (f"{HY}/apply/applyView.hc?recuYy={o.get('recuYy')}"
                    f"&recuType={o.get('recuType')}&recuCls={o.get('recuCls')}")
        logo = _text(o.get("logoNm"))
        tm = re.sub(r"\D", "", str(o.get("applyEndTm") or ""))
        tm = f"{tm[:2]}:{tm[2:4]}" if len(tm) == 4 else ""
        out.append({
            "company": "현대자동차" if logo in ("", "현대") else logo,
            "title": _text(o.get("recuNoticeNm")),
            "href": href,
            "block": _join(o.get("channelCodeNm"),
                           f"접수 {_ymd(o.get('applyStartDt'))} ~ {_ymd(o.get('applyEndDt'))} {tm}",
                           o.get("workPlaceCodeNm"), o.get("secCodeNm"), o.get("fldCodeNm"), o.get("collectMark")),
        })
    return out


# ───────────────────────── HD현대 ─────────────────────────
def fetch_hd() -> list:
    s = Session()
    d = s.json("https://recruit.hd.com/api/v1/jobda/getRecruitNoticeList?isPost=true&LANG=KR",
               headers={"Referer": "https://recruit.hd.com/kr/mainLayout/apply"})
    now = _now_kst().strftime("%Y-%m-%d %H:%M:%S")
    out = []
    for o in d.get("data") or []:
        st, en = o.get("receiveStartDatetime") or "", o.get("receiveEndDatetime") or ""
        if not o.get("isPost") or (en and en < now) or (st and st > now):
            continue
        secs = o.get("recruitSectorList") or []
        comps = sorted({x.get("companyName") for x in secs if x.get("companyName")})
        areas = sorted({x.get("area") for x in secs if x.get("area")})
        jobs = sorted({"/".join(filter(None, (x.get("job"), x.get("jobDetail")))) for x in secs} - {""})
        out.append({
            "company": comps[0] if len(comps) == 1 else (o.get("companyCategory") or "HD현대"),
            "title": _text(o.get("recruitNoticeName")),
            "href": o.get("recruitNoticeUrl") or "https://recruit.hd.com/kr/mainLayout/apply",
            "block": _join(o.get("recruitClassName"), o.get("recruitTypeName"), f"접수 {st[:16]} ~ {en[:16]}",
                           ", ".join(areas)[:120], ", ".join(sorted(jobs, key=lambda x: not EHS_FIRST.search(x))), ", ".join(comps)[:120]),  # 직무 목록 자르지 않음, 환경·안전 직무를 앞으로 (2026-10-09 백테스트: 200자 자르기로 HD 경력 2건 누락)
        })
    return out


# ───────────────────────── 롯데 ─────────────────────────
LOTTE = "https://recruit.lotte.co.kr"


def fetch_lotte() -> list:
    t = Session().request(LOTTE + "/apply/announcement")
    m = re.search(r"전체\((\d+)\)", t)
    total = int(m.group(1)) if m else None
    out = []
    for li in re.findall(r'<div class="job-card-group">(.*?)</li>', t, re.S):
        a = re.search(r'href="(/apply/announcement/detail/(\d+))[^"]*">(.*?)</a>', li, re.S)
        if not a:
            continue
        kind = re.search(r'class="ico-bage-anncmtype">(.*?)</span>', li, re.S)
        comp = re.search(r'class="cmp-name">(.*?)</div>', li, re.S)
        date = re.search(r'class="date">(.*?)</p>', li, re.S)
        dday = re.search(r'class="dday[^"]*">(.*?)</p>', li, re.S)
        out.append({
            "company": _text(comp.group(1)) if comp else "롯데",
            "title": _text(a.group(3)),
            "href": LOTTE + a.group(1),
            "block": _join(kind and kind.group(1), date and ("접수 " + _text(date.group(1))), dday and dday.group(1)),
        })
    if total is not None and total != len(out):
        print(f"[lotte] 경고: 전체({total}) 인데 {len(out)}건 읽음", file=sys.stderr)
    return out


# ───────────────────────── CJ ─────────────────────────
CJ = "https://recruit.cj.net/recruit/ko/recruit/recruit"
_CJ_TARGET = {"A": "신입", "B": "경력", "C": "인턴", "E": "신입·경력", "K": "기타"}
_CJ_TYPE = {"A": "정규직", "B": "계약직", "C": "기타", "D": "파견직"}


def fetch_cj() -> list:
    s = Session()
    hdr = {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest", "AjaxType": "FrameOne",
           "Accept": "application/json, text/javascript, */*; q=0.01", "Referer": CJ + "/list.fo"}
    items, total, page = [], None, 1
    while True:
        body = {"pageVal": str(page), "pageIndex": "200", "orderDesc": "1", "sch_title": "", "arrGubun": "",
                "arrRecBu": "", "arrRecJob": "", "arrRecArea": "", "schArea": "N"}
        lst = s.json(CJ + "/searchNewGonggoList.fo", data=body, headers=dict(hdr)).get("ds_newRecruitList") or []
        items.extend(lst)
        if lst and total is None:
            total = int(lst[0].get("tot_cnt") or 0)
        if not lst or len(items) >= (total or 0) or page >= 10:
            break
        page += 1
    out = []
    for o in items:
        if o.get("zz_close_yn") == "Y" or str(o.get("dday")) == "00000":
            continue
        num = o.get("zz_jo_num")
        href = (f"{CJ}/bestDetail.fo?direct=N&zz_jo_num={num}" if str(o.get("gubun")) == "2"
                else f"{CJ}/detail.fo?zz_jo_num={num}")
        period = (f"{o.get('zz_str_dt_str')} ~ 채용시까지" if o.get("zz_till_hire") == "Y"
                  else f"{o.get('zz_str_dt_str')} ~ {o.get('zz_end_dt_str')} {o.get('zz_end_hh') or ''}:{o.get('zz_end_mi') or ''}")
        out.append({
            "company": _text(o.get("compnm")) or "CJ",
            "title": _text(o.get("many_lng_zz_title") or o.get("zz_title")),
            "href": href,
            "block": _join(_CJ_TARGET.get(o.get("zz_target_1"), ""),
                           _CJ_TYPE.get(o.get("zz_jo_type"), "") if str(o.get("gubun")) == "2" else "",
                           "접수 " + period, o.get("location_cd_nm"), o.get("job_cd_nm")),
        })
    return out


# ───────────────────────── 두산 ─────────────────────────
DOOSAN = "https://career.doosan.com"


def fetch_doosan() -> list:
    t = Session().request(DOOSAN + "/dsp/sa/RecList.jsp")
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]*onclick="goDetail\(\'(\d+)\',\s*\'([^\']*)\',\s*\'([^\']*)\',\s*\'([^\']*)\'\);"'
                         r'[^>]*class="list-tit">(.*?)</a>', t, re.S):
        rec_id, body = m.group(1), m.group(5)
        if rec_id in seen:
            continue
        seen.add(rec_id)
        comp = re.search(r'<div class="company">(.*?)<span', body, re.S)
        kind = re.search(r'class="badge-type">(.*?)</span>', body, re.S)
        title = re.search(r"<strong>(.*?)</strong>", body, re.S)
        dl = re.search(r'class="deadline">(.*?)</div>', body, re.S)
        out.append({
            "company": _text(comp.group(1)) if comp else "두산",
            "title": _text(title.group(1)) if title else "",
            "href": f"{DOOSAN}/dsp/sa/RecForm.jsp?REC_ID={rec_id}&viewType=AD",
            "block": _join(kind and kind.group(1), dl and ("접수 " + _text(dl.group(1)))),
        })
    return out


# ──────────────── 마이다스 구형 appsite (효성 · LS MnM · 가온전선) ────────────────
def _appsite(tenant: str, company: str) -> list:
    base = f"https://{tenant}.recruiter.co.kr"
    s = Session()
    t = s.request(base + "/appsite/company/index")
    sn = re.search(r'id="appsiteSn" value="(\d+)"', t)
    st = re.search(r'id="settingType" value="(\w*)"', t)
    if not sn:
        raise RuntimeError(f"{tenant}: appsite 가 아님(신형 /career 로 이동했을 수 있음)")
    d = s.json(base + "/appsite/company/getMainView",
               data=urllib.parse.urlencode({"appsiteSn": sn.group(1), "settingType": st.group(1) if st else ""}),
               headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "X-Requested-With": "XMLHttpRequest", "Referer": base + "/appsite/company/index"})
    out = []
    for o in (d.get("jobnoticeInProgressList") or []) + (d.get("jobnoticePenddingList") or []):
        if o.get("receiptState") and o.get("receiptState") != "접수중":
            continue
        name = _text(o.get("jobnoticeName"))
        m = re.match(r"\s*\[([^\]]+)\]", name)
        out.append({
            "company": m.group(1) if m else company,
            "title": name,
            "href": f"{base}/app/jobnotice/view?systemKindCode={o.get('systemKindCode') or 'MRS2'}"
                    f"&jobnoticeSn={o.get('jobnoticeSn')}",
            "block": _join(o.get("recruitClassName"), o.get("recruitTypeName"),
                           f"접수 {str(o.get('applyStartDate') or '')[:16]} ~ {str(o.get('applyEndDate') or '')[:16]}",
                           o.get("receiptState")),
        })
    return out


def fetch_hyosung() -> list:
    return _appsite("hyosung", "효성")


def fetch_ls() -> list:
    return _appsite("lsmnm", "LS MnM") + _appsite("gaoncable", "가온전선")


# ───────────────────────── 에코프로 ─────────────────────────
ECOPRO = "https://ecoprorecruit.co.kr"


def fetch_ecopro() -> list:
    d = Session().json(ECOPRO + "/eco_pro/api/user/apply/list",
                       headers={"Referer": ECOPRO + "/eco_pro/page/user/recruit"})
    if not d.get("success"):
        raise RuntimeError(f"ecopro: success=false {str(d)[:200]}")
    now = _now_kst().strftime("%Y%m%d%H%M")
    out = []
    for o in d.get("data") or []:
        if o.get("openYn") not in (None, "Y") or (o.get("eDate") and str(o["eDate"]) < now):
            continue
        out.append({
            "company": _text(o.get("company")) or "에코프로",
            "title": _text(o.get("title")),
            "href": f"{ECOPRO}/eco_pro/page/user/apply_view?target={o.get('id')}",
            "block": _join(o.get("recType"), o.get("recTaskType"), "접수 " + _text(o.get("term")),
                           o.get("region"), (o.get("tagKeyword") or "").replace("#", ", "), o.get("status")),
        })
    return out


# ───────────────────────── 고려아연 ─────────────────────────
KZ = "https://careers.koreazinc.co.kr"


def fetch_koreazinc() -> list:
    s = Session()
    items, page, pages = [], 1, 1
    while page <= pages and page <= 10:
        d = s.json(f"{KZ}/v1/admin/recruit/recruit-announce/api/getList?rowsPerPage=50&noticeYn=Y&page={page}",
                   headers={"X-Requested-With": "XMLHttpRequest", "Referer": KZ + "/recruit/announce"})
        items.extend(d.get("content") or [])
        pages = int(d.get("totalPages") or 1)
        # 최신순 정렬: 이번 페이지가 전부 마감이면 뒤 페이지도 마감 → 중단
        if all(o.get("progressStatus") == "END" for o in d.get("content") or []):
            break
        page += 1
    out = []
    for o in items:
        if o.get("progressStatus") == "END":
            continue
        out.append({
            "company": _text(o.get("recruitCompNm")) or "고려아연",
            "title": _text(o.get("recruitNoticeName")),
            "href": f"{KZ}/recruit/announceView?recruitNoticeSn={o.get('recruitNoticeSn')}",
            "block": _join(o.get("recruitClassName"), o.get("recruitTypeName"),
                           f"접수 {str(o.get('receiveStartDatetime') or '')[:16]} ~ {str(o.get('receiveEndDatetime') or '')[:16]}",
                           o.get("recruitAreaName"), o.get("recruitDeptNm"), o.get("recruitJobNm"), o.get("recruitDDay")),
        })
    return out


# ───────────────────────── 금호석유화학 ─────────────────────────
KKPC = "https://recruit.kkpc.com/main/recruit/27_4"


def fetch_kumho() -> list:
    s = Session(cookies=True)
    idx = s.request(KKPC + "/index.jsp")
    mp = re.search(r'id="mempfstr" name="mempfstr" value="([^"]*)"', idx)
    t = s.request(KKPC + "/menu2_6.jsp",
                  data=urllib.parse.urlencode({"menufg": "2", "smenufg": "6", "mempfstr": mp.group(1) if mp else ""}),
                  headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": KKPC + "/index.jsp"})
    ul = re.search(r'<ul class="recruitList">(.*?)</ul>\s*(?:<div|<!--|</div)', t, re.S)
    out = []
    for li in re.findall(r"<li>(.*?)</li>", ul.group(1) if ul else t, re.S):
        a = re.search(r"f_jobread\('(\d+)'\)", li)
        title = re.search(r'<h4 class="title">(.*?)</h4>', li, re.S)
        if not (a and title):
            continue
        badges = [_text(b) for b in re.findall(r'<span class="badge">(.*?)</span>', li, re.S)]
        date = re.search(r'<p class="date">(.*?)</p>', li, re.S)
        if date and "마감" in _text(date.group(1)):
            continue
        out.append({
            "company": "금호석유화학",
            "title": _text(title.group(1)),
            "href": f"{KKPC}/menu2_6view.jsp?drno={a.group(1)}",
            "block": _join(" / ".join(b for b in badges if b), date and _text(date.group(1)).replace("|", "·")),
        })
    return out


FETCHERS = {
    "현대자동차": fetch_hyundai,
    "HD현대": fetch_hd,
    "롯데": fetch_lotte,
    "CJ": fetch_cj,
    "두산": fetch_doosan,
    "효성": fetch_hyosung,
    "LS": fetch_ls,
    "에코프로": fetch_ecopro,
    "고려아연": fetch_koreazinc,
    "금호석유화학": fetch_kumho,
}

EHS = re.compile(r"환경|안전|EHS|SHE|HSE|보건|화학물질|PSM")

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    only = sys.argv[1:]
    grand = 0
    for name, fn in FETCHERS.items():
        if only and name not in only:
            continue
        t0 = time.time()
        try:
            jobs = fn()
        except Exception as e:
            print(f"[{name}] 실패: {e!r}")
            continue
        hits = [j for j in jobs if EHS.search(j["title"] + " " + j["block"])]
        grand += len(jobs)
        print(f"[{name}] {len(jobs)}건, EHS {len(hits)}건 ({time.time() - t0:.0f}s)")
        for j in jobs[:1]:
            print("   예:", j)
        for j in hits:
            print("   *", j["company"], "|", j["title"], "|", j["block"][:90], "|", j["href"])
    print("합계:", grand)
