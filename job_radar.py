# -*- coding: utf-8 -*-
"""
환경안전 채용 레이더 (PC 실행판)

내 PC의 실제 브라우저(Chromium)로 채용 사이트를 열어 공고를 모으고,
어제까지 본 목록(radar.db)과 비교해 처음 보는 공고만 '신규'로 알려준다.

사용법
  python job_radar.py            # 수집 → 비교 → 리포트(radar.html) → 알림
  python job_radar.py --debug    # 사이트별 화면 텍스트를 debug/ 폴더에 저장 (수집이 0건인 사이트 점검용)
  python job_radar.py --only 사람인   # 이름에 '사람인'이 들어간 소스만 실행
  python job_radar.py --show     # 수집 없이 마지막 리포트만 열기
"""
import argparse
import asyncio
import datetime as dt
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import webbrowser
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "ml"))
# 자주 바뀌는 파일(DB·리포트·디버그)은 OneDrive 동기화 충돌을 피하려고 PC 로컬 폴더에 쓴다.
# 코드·설정·학습 데이터는 이 폴더(OneDrive 가능)에 그대로 둔다.
def _data_dir():
    if os.environ.get("JOBRADAR_DATA"):  # 테스트·특수 실행용
        d = Path(os.environ["JOBRADAR_DATA"])
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        d = Path(os.environ["LOCALAPPDATA"]) / "JobRadar"
    else:
        d = BASE / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


DATA = _data_dir()
DB_PATH = DATA / "radar.db"
REPORT = DATA / "radar.html"
DEBUG_DIR = DATA / "debug"
KST = dt.timezone(dt.timedelta(hours=9))


def today():
    return dt.datetime.now(KST).date()


def load_json(name):
    with open(BASE / name, encoding="utf-8") as f:
        return json.load(f)


def load_settings():
    """settings.json + settings.local.json(개인 값: 현재 회사 등 — 공개 저장소에 올리지 않음)을 위쪽 키 단위로 덮어쓴다."""
    s = load_json("settings.json")
    local = BASE / "settings.local.json"
    if local.exists():
        for k, v in json.loads(local.read_text(encoding="utf-8")).items():
            s[k] = {**s[k], **v} if isinstance(v, dict) and isinstance(s.get(k), dict) else v
    return s


# ---------------------------------------------------------------- 브라우저에서 공고 후보 뽑기
# 사이트마다 HTML 구조가 달라서, 특정 사이트용 선택자 대신 "키워드가 들어간 링크/항목 + 그 주변 텍스트"를 모은다.
EXTRACT_JS = r"""
(pattern) => {
  const re = new RegExp(pattern, 'i');
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [], seen = new Set();
  const grow = (el, base) => {
    let block = base, c = el;
    for (let i = 0; i < 7 && c.parentElement; i++) {
      c = c.parentElement;
      const t = clean(c.innerText);
      if (t.length > 700) break;
      // 부모가 다른 공고 링크까지 품기 시작하면 멈춘다 (옆 공고 텍스트가 섞이는 것 방지)
      let others = 0;
      for (const b of c.querySelectorAll('a')) { if (b !== el && re.test(clean(b.innerText || b.textContent)) && !el.contains(b)) { others++; break; } }
      if (others) break;
      if (c.querySelectorAll('a').length > 6) break;
      block = t;
    }
    return block;
  };
  for (const a of document.querySelectorAll('a')) {
    const t = clean(a.innerText || a.textContent);
    if (t.length < 4 || t.length > 220 || !re.test(t)) continue;
    const href = a.href || '';
    const key = t + '|' + href;
    if (seen.has(key)) continue; seen.add(key);
    out.push({title: t, href, block: grow(a, t)});
  }
  if (out.length === 0) {
    // 링크 없이 클릭 이벤트로 동작하는 포털용: 목록 항목처럼 보이는 요소를 훑는다
    const sel = 'li, tr, article, [class*="item"], [class*="Item"], [class*="card"], [class*="Card"], [class*="list"] > div, [role="listitem"], [onclick]';
    for (const el of document.querySelectorAll(sel)) {
      const t = clean(el.innerText);
      if (t.length < 6 || t.length > 400 || !re.test(t)) continue;
      if (seen.has(t)) continue; seen.add(t);
      const first = clean((el.querySelector('h1,h2,h3,h4,h5,strong,b,[class*="tit"],[class*="Tit"]') || el).innerText).slice(0, 200);
      out.push({title: first || t.slice(0, 120), href: '', block: t});
    }
  }
  return {items: out, textLen: clean(document.body ? document.body.innerText : '').length, anchors: document.querySelectorAll('a').length};
}
"""


import random
from urllib.parse import urlparse

RETRY_ERRORS = ("ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_TIMED_OUT", "Timeout", "ERR_EMPTY_RESPONSE", "ERR_TIMED_OUT",
                "Connection reset", "timed out", "HTTP 429", "HTTP 50", "IncompleteRead", "RemoteDisconnected", "10054", "urlopen error", "EOF occurred", "Remote end closed")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"


def http_get(url, timeout):
    """브라우저 없이 HTML만 받는다. 사람인은 자동화 브라우저(헤드리스 크롬)의 연결을 끊지만 일반 HTTP 요청은 받아 준다 (2026-10-04 실측)."""
    import gzip, urllib.request, urllib.error
    # gzip 필수: 사람인 검색 페이지는 2.5MB라 압축 없이 받으면 90초, 압축하면 9초 (2026-10-04 실측)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9", "Accept": "text/html,application/xhtml+xml", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            if r.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
            return body.decode(r.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {url}") from None


def static_html(raw, url):
    """받은 HTML을 네트워크 없는 페이지에 넣기 위해 스크립트를 빼고, 상대 링크가 원래 주소로 풀리도록 <base>를 단다."""
    raw = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", raw, flags=re.S | re.I)
    base = f'<base href="{html.escape(url, quote=True)}">'
    return re.sub(r"<head\b[^>]*>", lambda m: m.group(0) + base, raw, count=1, flags=re.I) if re.search(r"<head\b", raw, re.I) else base + raw


def log_source(res, t0):
    """사이트별 한 줄 로그: 시각 + 걸린 초 (어디서 시간이 새는지 log.txt 로 바로 보이게)"""
    print(f"  [{dt.datetime.now(KST):%H:%M:%S}] {'OK ' if res['ok'] else 'ERR'} {res['name']}: 후보 {len(res['items'])}건 "
          f"({time.monotonic() - t0:.1f}초) {res['error']}", flush=True)


async def fetch_source(ctx, src, pattern, timeout, debug, gsem, host_locks, gap, static_hosts=(), block_re=None, run=None):
    """같은 사이트는 한 번에 하나씩, 사람처럼 쉬어 가며 연다. 끊기면 쉬었다가 재시도.
    run: {'deadline': 전체 실행 마감(monotonic), 'source_timeout': 사이트당 초, 'blocked': {호스트: BLOCKED 횟수}, 'block_limit': N}"""
    run = run if run is not None else {}
    host = urlparse(src["url"]).netloc
    res = {"name": src["name"], "type": src["type"], "url": src["url"], "ok": False, "items": [], "error": "", "textLen": 0, "anchors": 0}
    # 사이트 잠금을 먼저 잡고 나서 전역 자리를 잡는다. 반대 순서면 같은 사이트(사람인 284곳) 대기자들이
    # 전역 자리를 모두 차지한 채 잠금을 기다려서, 다른 사이트까지 사람인 속도로 직렬화된다.
    async with host_locks.setdefault(host, asyncio.Lock()), gsem:
        t0 = time.monotonic()
        if run.get("deadline") and t0 > run["deadline"]:
            res["error"] = "SKIPPED 전체 실행 시간 한도(max_run_minutes) 도달"
            log_source(res, t0)
            return res
        blocked = run.setdefault("blocked", {})
        if blocked.get(host, 0) >= int(run.get("block_limit", 3)):
            res["error"] = f"SKIPPED 같은 사이트 BLOCKED {blocked[host]}회 → 이번 실행에서 건너뜀"
            log_source(res, t0)
            return res
        try:
            # 모음 검색(검색어 14개 × 여러 쪽)은 소스별 timeout_sec 로 더 길게
            await asyncio.wait_for(_fetch_page(ctx, src, pattern, timeout, debug, host in static_hosts, block_re, res),
                                   timeout=float(src.get("timeout_sec") or run.get("source_timeout", 150)))
        except asyncio.TimeoutError:
            res.update(ok=False, items=[], error=f"TIMEOUT 사이트당 {run.get('source_timeout', 150)}초 초과")
        if res["error"].startswith("BLOCKED"):
            blocked[host] = blocked.get(host, 0) + 1
        await asyncio.sleep(gap.get(host, gap["default"]) + random.uniform(0, 1.5))
    log_source(res, t0)
    return res


def run_adapter(src):
    """type 'api' 소스: portals/<모듈>.py 가 사이트 내부 API 로 공고 목록을 준다(대기업 채용 사이트는 JS 화면이라 HTML 로는 못 읽음, 2026-10-08).
    adapter 'samsung' → portals.samsung.fetch() / 'recruiter' + tenant → fetch(tenant, company) / 'groups:현대자동차그룹' → FETCHERS[키]()"""
    import importlib
    mod, _, key = src["adapter"].partition(":")
    m = importlib.import_module(f"portals.{mod}")
    if key:
        return m.FETCHERS[key]()
    if src.get("tenant"):
        return m.fetch(src["tenant"], src.get("company", ""))
    return m.fetch()


async def _fetch_api(src, pattern, debug, res):
    pat = re.compile(pattern, re.I)
    for attempt in range(2):
        try:
            raw = await asyncio.to_thread(run_adapter, src)
            items = [{"title": re.sub(r"\s+", " ", it.get("title", "")).strip(), "href": it.get("href", ""), "block": it.get("block", ""),
                      "company": it.get("company", "")} for it in raw if it.get("title")]
            # 후보 그물: 제목·본문 어디든 환경·안전 단어가 있으면 넘긴다(직무 판정은 judge 가 제목으로)
            items = [it for it in items if pat.search(it["title"] + " " + it["block"])]
            # 공고 0건은 정상일 수도(진행 중 공고 없음), 사이트 개편일 수도 있다 → 실패 대신 '내용 거의 없음'(노랑)으로 드러낸다
            res.update(items=items, textLen=(sum(len(it["block"]) + len(it["title"]) for it in raw) + 1000) if raw else 0,
                       anchors=len(raw), ok=True, error="" if raw else "API 0건 (진행 공고 없음 또는 사이트 개편)")
            if debug:
                DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                safe = re.sub(r'[\\/:*?"<>|\s]+', "_", src["name"])
                (DEBUG_DIR / f"{safe}_items.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
            return
        except Exception as e:
            res.update(ok=False, items=[], error=f"API {type(e).__name__}: {str(e).splitlines()[0][:160] if str(e) else ''}")
            if attempt == 0:
                await asyncio.sleep(10)


async def _fetch_page(ctx, src, pattern, timeout, debug, static, block_re, res):
    if src.get("type") == "api":
        return await _fetch_api(src, pattern, debug, res)
    for attempt in range(3):
        page = await ctx.new_page()
        try:
            if static:
                raw = await asyncio.to_thread(http_get, src["url"], timeout)
                # 네트워크는 전부 막는다: CSS·이미지 요청도 브라우저로 나가면 사람인이 끊어서 페이지가 멈춘다 (2026-10-04 실측)
                await page.route("**/*", lambda route: route.abort())
                await page.set_content(static_html(raw, src["url"]), wait_until="domcontentloaded", timeout=timeout * 1000)
            else:
                await page.goto(src["url"], timeout=timeout * 1000, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                for _ in range(3):  # 아래로 내려야 목록이 더 뜨는 페이지 대비
                    await page.mouse.wheel(0, 4000)
                    await page.wait_for_timeout(700)
            data = await page.evaluate(EXTRACT_JS, pattern)
            body = await page.evaluate("document.body ? document.body.innerText.slice(0, 3000) : ''")
            if block_re and data["textLen"] < 2000 and block_re.search(body):
                # Cloudflare 등 '사람인지 확인' 화면: 그 문구가 공고로 잡히지 않게 실패로 처리한다
                res.update(items=[], textLen=data["textLen"], anchors=data.get("anchors", 0), ok=False, error="BLOCKED 봇 확인·차단 화면")
            else:
                res.update(items=data["items"], textLen=data["textLen"], anchors=data.get("anchors", 0), ok=True, error="")
            if debug:
                DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                safe = re.sub(r'[\\/:*?"<>|\s]+', "_", src["name"])
                txt = await page.evaluate("document.body ? document.body.innerText : ''")
                (DEBUG_DIR / f"{safe}.txt").write_text(txt, encoding="utf-8")
                links = await page.evaluate("[...document.querySelectorAll('a')].map(a=>(a.innerText||'').replace(/\\s+/g,' ').trim()+'  ->  '+a.href).filter(x=>x.length>6).slice(0,400).join('\\n')")
                (DEBUG_DIR / f"{safe}_links.txt").write_text(links, encoding="utf-8")
                (DEBUG_DIR / f"{safe}_items.json").write_text(json.dumps(data["items"], ensure_ascii=False, indent=1), encoding="utf-8")
                await page.screenshot(path=str(DEBUG_DIR / f"{safe}.png"), full_page=False)
            break
        except Exception as e:
            res["error"] = str(e).splitlines()[0][:200]
            if attempt < 2 and any(k in res["error"] for k in RETRY_ERRORS):
                await asyncio.sleep((15, 45)[attempt] + random.uniform(0, 5))
                continue
            break
        finally:
            await page.close()


async def crawl(sources, settings, debug):
    from playwright.async_api import async_playwright
    b = settings.get("browser", {})
    pattern = settings["candidate_pattern"]
    gsem = asyncio.Semaphore(int(b.get("concurrency", 4)))
    host_locks = {}
    gap = {"default": float(b.get("host_gap_sec", 2.0)), **{h: float(v) for h, v in b.get("host_gap_overrides", {}).items()}}
    static_hosts = set(b.get("static_hosts", []))
    block_re = re.compile(settings["block_page_pattern"], re.I) if settings.get("block_page_pattern") else None
    async with async_playwright() as p:
        launch = dict(headless=bool(b.get("headless", True)), args=["--disable-blink-features=AutomationControlled"])
        browser = None
        if b.get("use_installed_chrome", True):
            try:
                browser = await p.chromium.launch(channel="chrome", **launch)  # 설치된 크롬을 쓰면 차단이 덜하다
            except Exception:
                browser = None
        if browser is None:
            browser = await p.chromium.launch(**launch)
        ctx = await browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1366, "height": 900})
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        # 사이트 순서를 섞어 같은 사이트가 연달아 몰리지 않게 한다
        order = list(range(len(sources))); random.shuffle(order)
        # 전체 실행 한도: 넘으면 새 사이트는 시작하지 않고(SKIPPED) 모은 것까지 저장한다. 사이트당 한도는 TIMEOUT 처리.
        run = {"deadline": time.monotonic() + float(settings.get("max_run_minutes", 150)) * 60,
               "source_timeout": float(b.get("source_timeout_sec", 150)), "blocked": {}, "block_limit": int(b.get("blocked_host_limit", 3))}
        tasks = {i: asyncio.create_task(fetch_source(ctx, sources[i], pattern, int(b.get("timeout_sec", 35)), debug, gsem, host_locks, gap, static_hosts, block_re, run)) for i in order}
        results = [await tasks[i] for i in range(len(sources))]
        await browser.close()
    return results


# ---------------------------------------------------------------- 텍스트에서 날짜·경력 읽기
def guess_year(mo, t):
    """연도 없이 월·일만 있을 때: 오늘과 6개월 넘게 차이 나면 해가 바뀐 것으로 본다 (10월에 '03.15' → 내년, 2월에 '12.20' → 작년)."""
    if mo > t.month + 6:
        return t.year - 1
    if mo < t.month - 6:
        return t.year + 1
    return t.year


def ko_dates(s):
    """'2026년 10월 12일' · '10월 12일' → '2026.10.12' · '10.12' (그룹 채용 사이트 API 표기, 2026-10-08 SK 실례)"""
    s = re.sub(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", r"\1.\2.\3", s)
    return re.sub(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일", r"\1.\2", s)


def parse_deadline(block, t):
    block = ko_dates(block)
    # '채용시' 는 '채용시스템'·'채용시작' 과 구분한다 (2026-10-08: '채용시스템' 문구로 마감일 있는 공고가 '상시' 처리됨)
    if re.search(r"상시\s*채용|상시모집|채용\s*시\s*마감|채용시(?![스작])", block):
        return "상시"
    # 기간(시작 ~ 끝)이면 끝 날짜가 마감일. 시작일 뒤의 시각('09:00')을 마감으로 읽어 '마감 지남'으로 버리던 문제 (2026-10-08)
    m = re.search(r"~\s*(?:(20\d{2})[.\-/]\s*)?(\d{1,2})[.\-/]\s*(\d{1,2})(?!\d)", block)
    if m:
        mo, d = int(m.group(2)), int(m.group(3))
        y = int(m.group(1)) if m.group(1) else guess_year(mo, t)
        try:
            return dt.date(y, mo, d).isoformat()
        except ValueError:
            pass
    m = re.search(r"(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})\s*(?:\([^)]*\))?\s*(?:까지|마감|\d{1,2}:\d{2})", block)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"D\s*-\s*(\d{1,3})\b", block)
    if m:
        return (t + dt.timedelta(days=int(m.group(1)))).isoformat()
    if re.search(r"오늘\s*마감|D-?day", block, re.I):
        return t.isoformat()
    return ""


def parse_posted(block, t):
    block = ko_dates(block)
    m = re.search(r"(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})\s*(?:\([^)]*\))?\s*~", block)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{1,3})\s*일\s*전\s*(?:등록|게시|업데이트)?", block)
    if m:
        return (t - dt.timedelta(days=int(m.group(1)))).isoformat()
    if re.search(r"(\d{1,2})\s*(?:시간|분)\s*전|오늘\s*등록|방금", block):
        return t.isoformat()
    m = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*(?:등록|게시)", block)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def parse_exp(block):
    """(표시용 문자열, 최소 연차 또는 None, 신입만인지)"""
    any_ = re.search(r"경력\s*무관|신입\s*[·/,]\s*경력|신입\s*/\s*경력|경력\s*[·/]\s*신입", block)
    m = re.search(r"경력\s*(\d{1,2})\s*년\s*(?:↑|이상)", block) or re.search(r"(\d{1,2})\s*년\s*(?:↑|이상)", block)
    r = re.search(r"경력\s*(\d{1,2})\s*~\s*(\d{1,2})\s*년", block) or re.search(r"(\d{1,2})\s*~\s*(\d{1,2})\s*년\s*(?:차)?", block)
    if r:
        return f"{r.group(1)}~{r.group(2)}년", int(r.group(1)), False
    if m:
        return f"{m.group(1)}년 이상", int(m.group(1)), False
    if any_:
        return "신입·경력 / 무관", 0, False
    if re.search(r"경력", block) and not re.search(r"신입", block):
        return "경력", None, False
    if re.search(r"신입", block):
        return "신입", 0, True
    return "확인 불가", None, False


def has_name(name, text):
    """회사명이 text 에 들어 있는지. 영문 이름은 앞뒤가 다른 영문자면 다른 회사로 본다 (HL ≠ HLB제넥스, SK ≠ GSK)."""
    pat = re.escape(name.lower())
    if re.search(r"[a-z0-9]$", name.lower()):
        pat += r"(?![a-z])"
    if re.match(r"[a-z0-9]", name.lower()):
        pat = r"(?<![a-z])" + pat
    return re.search(pat, (text or "").lower()) is not None


# '(삼성內)', '삼성전자 협력사', 'SK 사업장 내 상주' 처럼 납품·상주처를 가리키는 회사명은 공고를 낸 회사가 아니다
VENDOR_CTX = re.compile(r"[A-Za-z0-9가-힣&]+\s*(?:內|사업장\s*내|협력\s*(?:사|업체)|상주|1차\s*벤더|2차\s*벤더|사내\s*하도급)")


def find_company(block, settings):
    text = VENDOR_CTX.sub(" ", block)
    names = sorted(settings["large_companies"] + settings["mid_companies"], key=len, reverse=True)
    for n in names:
        if has_name(n, text):
            return n
    return ""


_GROUPS = None


def large_group_of(name):
    """공정위 지정 대기업집단 소속회사 명단(data/large_groups.json)에서 회사명 전체 일치로 그룹명. 없으면 ''."""
    global _GROUPS
    if _GROUPS is None:
        _GROUPS = {}
        p = BASE / "data" / "large_groups.json"
        if p.exists():
            norm = lambda s: re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\s|[()·.,（）\-]", "", s or "").lower()
            for g in json.loads(p.read_text(encoding="utf-8")).get("groups", []):
                for a in g.get("affiliates_detail", []):
                    for n in [a.get("name"), a.get("norm"), *a.get("aliases", [])]:
                        if n:
                            _GROUPS[norm(n)] = g["group"]
            _GROUPS["__norm__"] = norm
    if not _GROUPS:
        return ""
    return _GROUPS.get(_GROUPS["__norm__"](name), "")


def is_large(name, settings):
    return any(has_name(k, name) for k in settings["large_companies"]) or bool(large_group_of(name))


def size_rank(src, company, settings):
    """1 대기업 · 2 대기업 계열·외국계 대기업 · 4 중견 · 5 기타 (작을수록 위)"""
    if src.get("type") == "portal" or is_large(company, settings):
        return 1
    if src.get("group") == "대기업 계열":
        return 2
    if any(has_name(k, company) for k in settings["mid_companies"]):
        return 4
    return 5


SIZE_LABEL = {1: "대기업", 2: "대기업 계열", 3: "공기업", 4: "중견", 5: "기타"}


def normalize_title(t):
    t = re.sub(r"\[[^\]]*\]|\([^)]*마감[^)]*\)|D-\d+|~\s*\d{1,2}[./]\d{1,2}", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# 공고 '후보'가 실제 공고인지: 2026-10-04 실측 — 포털의 설명 문장(“우대자격증 : 산업안전기사”), 사람인 직무분류·회사소개 링크,
# '(검색)' 소스의 다른 회사 공고가 공고로 저장되던 문제
RECRUIT_WORD = re.compile(r"채용|모집|공고|구인|공채|recruit|hiring|사원|경력직|신입|인턴|담당자|엔지니어|매니저|관리자\s*(?:채용|모집)|팀원|팀장|Specialist|Engineer|Manager|Lead", re.I)
CARD_WORD = re.compile(r"담당\s*업무|주요\s*업무|자격\s*요건|지원\s*자격|우대\s*사항|근무지|근무\s*지역|전공|학력|모집\s*(?:분야|인원|부문)|접수|마감")
NOT_POSTING = [  # (호스트 일부, 공고 주소 표시) — 이 사이트 링크인데 공고 주소 표시가 없으면 목록·분류·회사 페이지
    ("saramin.co.kr", re.compile(r"rec_idx=")), ("jobkorea.co.kr", re.compile(r"GI_Read|/Recruit/")),
    ("incruit.com", re.compile(r"jobpost|jobdb_info")),
]


def not_a_posting(item, src):
    href, title, block = item.get("href", ""), item["title"], item["block"]
    if href:
        host = urlparse(href).netloc
        for h, ok in NOT_POSTING:
            if h in host:
                return "" if ok.search(href) else "공고 링크 아님(분류·회사·검색 페이지)"
        # 그 밖의 사이트(그룹 채용 포털 등)의 링크: 홍보 문구(“…당신의 동료 ○○○ 책임”, “근무환경소개”)를 거르기 위해 아래 규칙을 똑같이 적용
    # 링크 없는 후보(스크립트 포털)·포털 링크: 제목이 채용 문구이거나, 담당업무·자격 같은 공고 구조를 갖춘 카드만
    if RECRUIT_WORD.search(title):
        return ""
    if len(block) >= len(title) + 40 and len(set(m.group(0).replace(" ", "") for m in CARD_WORD.finditer(block))) >= 2:
        return ""
    return "공고가 아닌 설명 문구"


def norm_co(s):
    return re.sub(r"\(주\)|㈜|주식회사|\s|[()·.,（）]", "", s or "").lower()


# 그룹 채용 사이트 본문(직무 목록)에서 볼 '확실한' 환경·안전 직무명 — '친환경 설계' 같은 낱말은 해당 없음
API_FIELD = re.compile(r"환경\s*안전|안전\s*환경|안전\s*보건|산업\s*안전|공정\s*안전|안전\s*관리|보건\s*관리|환경\s*관리|화학\s*물질\s*관리|EHS|SHE|HSE|PSM")

def judge(src, item, settings, t):
    title, block = item["title"], item["block"]
    from relevance import sections_loose, sections
    if src.get("type") == "api" and not sections_loose({"title": title, "company": src.get("company", "")}, settings):
        # 그룹 채용 사이트: '경력사원 상시채용'처럼 제목엔 분야가 없고 직무 목록에만 '환경안전'이 있는 공고 (2026-10-09, OCI 사례)
        m = API_FIELD.search(block)
        if m:
            title = f"{title} ({m.group(0)})"
    if not sections_loose({"title": title, "company": src.get("company", "")}, settings):
        return None, "직무 규칙 불통과"
    why = "" if src["type"] == "api" else not_a_posting(item, src)  # API 목록은 그 자체가 공고 목록
    if why:
        return None, why
    # '(검색)' 소스: 카드에 회사명이 안 보이면 다른 회사 공고일 수 있다 → 원문 확인 때 정식 회사명으로 가린다(바로 버리지 않음)
    check_company = src["type"] == "company" and bool(re.search(r"/search|searchword=|stext=", src["url"])) and norm_co(src.get("company")) not in norm_co(block + title)
    if src["type"] == "search":
        company = find_company(block, settings)
        if not company:
            return None, "중견 이상 목록에 없는 회사"
    elif src["type"] == "api":
        company = item.get("company") or src.get("company", "")
        if src.get("size_filter") and not (find_company(company, settings) or is_large(company, settings)):
            return None, "중견 이상 목록에 없는 회사"  # 사람인 대기업 필터 같은 모음 검색용
        if src.get("groups_only") and not large_group_of(company):
            return None, "대기업집단 계열사 아님"   # 필터 없는 검색: 공정위 명단에 있는 계열사만
    else:
        company = src["company"]
    exp, min_years, newbie_only = parse_exp(block)
    if min_years is not None and min_years > int(settings["max_min_years"]):
        return None, f"경력 하한 {min_years}년"
    if newbie_only and not is_large(company, settings):
        return None, "중견기업 신입"
    deadline = parse_deadline(block, t)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline or "") and deadline < t.isoformat():
        return None, "마감 지남"
    if re.search(r"마감\s*된|접수\s*마감|채용\s*마감|모집\s*마감|closed", block, re.I) and not re.search(r"마감\s*(?:일|임박|D)", block):
        return None, "마감 표시"
    rank = size_rank(src, company, settings)
    if src.get("default_rank") and rank == 5:  # 기업형태 필터(대기업·매출1000대·중견…)가 걸린 모음 검색: 목록에 없는 회사는 '중견 이상'으로만.
        rank = int(src["default_rank"])         # 진짜 대기업 여부는 사람인 기업정보(company_form)로 export 때 올린다 — 필터 묶음이 넓어 '대기업'으로 단정 못 함
    if re.search(r"공사|공단|공공기관|진흥원|안전원", company):
        rank = min(rank, 3)
    sec = sections({"title": title, "company": company}, settings, rank)
    if not sec:
        return None, "직무 규칙 불통과(분야별 제외)"
    return {
        "sec": sec,
        "size_rank": rank,
        "size": SIZE_LABEL[rank],
        "company": company,
        "title": normalize_title(title),
        "track": "신입" if newbie_only else "경력",
        "exp": exp,
        "posted": parse_posted(block, t),
        "deadline": deadline,
        "link": item["href"] or src["url"],
        "source": src["name"],
        "snippet": block[:300],
        "check_company": check_company,
    }, ""


# ---------------------------------------------------------------- 어제 목록과 비교 (radar.db)
def db_open():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS jobs(
        id TEXT PRIMARY KEY, company TEXT, title TEXT, track TEXT, exp TEXT, posted TEXT, deadline TEXT,
        link TEXT, source TEXT, snippet TEXT, first_seen TEXT, last_seen TEXT, status TEXT, mark TEXT DEFAULT '')""")
    for col, typ in (("size_rank", "INTEGER DEFAULT 5"), ("size", "TEXT DEFAULT ''"), ("rel_score", "REAL DEFAULT 0"), ("tier", "TEXT DEFAULT ''"),
                     # 원문 확인 값 (verify_jobs.py)
                     ("location", "TEXT DEFAULT ''"), ("edu", "TEXT DEFAULT ''"), ("emp_type", "TEXT DEFAULT ''"), ("deadline_time", "TEXT DEFAULT ''"),
                     ("company_full", "TEXT DEFAULT ''"), ("verify", "TEXT DEFAULT ''"), ("verified_at", "TEXT DEFAULT ''"), ("verify_tried_at", "TEXT DEFAULT ''"),
                     ("duty", "TEXT DEFAULT ''"), ("duty_evidence", "TEXT DEFAULT ''"), ("duty_flag", "TEXT DEFAULT ''"),
                     # 회사 비교 (company_info.py · 기준: settings.baseline)
                     ("csn", "TEXT DEFAULT ''"), ("company_form", "TEXT DEFAULT ''"), ("employees", "INTEGER DEFAULT 0"),
                     ("avg_salary", "INTEGER DEFAULT 0"), ("salary_year", "INTEGER DEFAULT 0"), ("cmp_salary", "TEXT DEFAULT ''"), ("cmp_size", "TEXT DEFAULT ''"),
                     # 분야(쉼표 구분: 환경,안전) — settings.profiles
                     ("sec", "TEXT DEFAULT ''")):
        try:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    con.execute("""CREATE TABLE IF NOT EXISTS runs(
        run_at TEXT PRIMARY KEY, new INTEGER, open INTEGER, sources_ok INTEGER, sources_total INTEGER, health TEXT)""")
    return con


def job_id(j):
    key = f"{j['company']}|{re.sub(r'[^0-9A-Za-z가-힣]', '', j['title'])}"
    return "j-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


# 사이트의 공고 번호: 같은 공고가 제목·회사명 표기만 달라 두 줄로 쌓이는 것을 막는 기준 (2026-10-08)
CANON_PATTERNS = [
    ("saramin", re.compile(r"saramin\.co\.kr/.*?[?&]rec_idx=(\d+)", re.I)),
    ("jobkorea", re.compile(r"jobkorea\.co\.kr/.*?GI_Read/(\d+)", re.I)),
    ("incruit", re.compile(r"incruit\.com/.*?jobpost\.asp\?(?:.*?&)?job=(\d+)", re.I)),
]


def canon_key(link):
    """링크에서 사이트 공고 번호를 꺼낸다: 'saramin:55100290' / 'jobkorea:4801234' / 'incruit:1234567'. 없으면 ''."""
    for site, pat in CANON_PATTERNS:
        m = pat.search(link or "")
        if m:
            return f"{site}:{m.group(1)}"
    return ""


def find_canon_row(con, key):
    """같은 공고 번호를 링크에 가진 기존 행의 id (진행 중인 행 우선, 그다음 처음 본 날이 이른 행)."""
    if not key:
        return None
    num = key.split(":", 1)[1]
    rows = con.execute("SELECT id, link, status, first_seen FROM jobs WHERE link LIKE ?", (f"%{num}%",)).fetchall()
    rows = [r for r in rows if canon_key(r[1]) == key]
    rows.sort(key=lambda r: (r[2] != "open", r[3] or ""))
    return rows[0][0] if rows else None


def resolve_id(con, j):
    """이 공고가 저장될(또는 저장된) 행 id. 기존 id 는 바꾸지 않는다: 공고 번호가 같은 행이 있으면 그 행, 없으면 회사|제목 해시."""
    jid = find_canon_row(con, canon_key(j.get("link", ""))) or job_id(j)
    row = con.execute("SELECT verify FROM jobs WHERE id=?", (jid,)).fetchone()
    if row and (row[0] or "").startswith("중복: "):  # tools/dedup_radar.py 가 합친 행 → 남긴 행으로
        jid = row[0][4:].strip() or jid
    return jid


# 원문 확인에서 '마감'·'다른 회사'로 닫힌 공고는 화면에 다시 보여도 다시 열지 않는다
CLOSED_BY_SOURCE = ("마감", "다른 회사")


def merge(con, jobs, t, failed_sources=()):
    """failed_sources: 이번 실행에서 실패(ERR·TIMEOUT·SKIPPED)한 소스 이름. 그 소스에서만 보던 공고는 7일 미관측 마감에서 뺀다
    (30일 넘게 안 보이면 그래도 닫는다)."""
    ts = t.isoformat()
    new_ids = []
    seen = set()
    for j in jobs:
        jid = resolve_id(con, j)
        if jid in seen:
            continue
        seen.add(jid)
        row = con.execute("SELECT deadline, posted, verified_at, verify FROM jobs WHERE id=?", (jid,)).fetchone()
        if j.get("closed"):  # 원문에 '접수 마감'으로 나온 공고: 새로 넣지 않고, 있던 것은 닫는다
            if row:
                con.execute("UPDATE jobs SET status='closed', verify=?, verified_at=COALESCE(NULLIF(?, ''), verified_at) WHERE id=?",
                            (j.get("verify", ""), j.get("verified_at", ""), jid))
            continue
        if row:
            # 원문 확인으로 닫힌 행은 이번에 원문을 새로 확인해 열려 있다고 나온 경우에만 다시 연다
            reopen = not (row[3] or "").startswith(CLOSED_BY_SOURCE) or bool(j.get("verified_at"))
            status_sql = "status='open'" if reopen else "status=status"
            if row[2] and not j.get("verified_at"):
                # 원문 확인 값(링크·마감·경력·등록일)이 있으면 화면 글자 추정값으로 덮지 않는다
                con.execute(f"""UPDATE jobs SET last_seen=?, {status_sql}, source=?, snippet=?, size_rank=?, size=?, rel_score=?, tier=? WHERE id=?""",
                            (ts, j["source"], j["snippet"], j["size_rank"], j["size"], j["rel_score"], j["tier"], jid))
            else:
                con.execute(f"""UPDATE jobs SET last_seen=?, {status_sql}, exp=?, link=?, source=?, snippet=?, size_rank=?, size=?, rel_score=?, tier=?,
                    deadline=COALESCE(NULLIF(?, ''), deadline), posted=COALESCE(NULLIF(posted, ''), ?) WHERE id=?""",
                            (ts, j["exp"], j["link"], j["source"], j["snippet"], j["size_rank"], j["size"], j["rel_score"], j["tier"], j["deadline"], j["posted"], jid))
        else:
            con.execute("""INSERT INTO jobs(id,company,title,track,exp,posted,deadline,link,source,snippet,first_seen,last_seen,status,mark,size_rank,size,rel_score,tier)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'',?,?,?,?)""",
                        (jid, j["company"], j["title"], j["track"], j["exp"], j["posted"], j["deadline"], j["link"],
                         j["source"], j["snippet"], ts, ts, "open", j["size_rank"], j["size"], j["rel_score"], j["tier"]))
            new_ids.append(jid)
        if j.get("verified_at"):  # 원문 확인 값은 화면 글자에서 읽은 추정값보다 우선
            con.execute("""UPDATE jobs SET location=?, edu=?, emp_type=?, deadline_time=?, company_full=?, verify=?, verified_at=?, duty=?, duty_evidence=?, duty_flag=?, tier=?,
                posted=COALESCE(NULLIF(?, ''), posted), deadline=COALESCE(NULLIF(?, ''), deadline), exp=COALESCE(NULLIF(?, ''), exp) WHERE id=?""",
                        (j.get("location", ""), j.get("edu", ""), j.get("emp_type", ""), j.get("deadline_time", ""), j.get("company_full", ""),
                         j.get("verify", ""), j["verified_at"], j.get("duty", ""), j.get("duty_evidence", ""), j.get("duty_flag", ""), j.get("tier", ""),
                         j.get("v_start", ""), j.get("v_deadline", ""), j.get("v_exp", ""), jid))
        elif j.get("verify"):
            con.execute("UPDATE jobs SET verify=?, duty=?, duty_evidence=?, duty_flag=? WHERE id=? AND verified_at=''",
                        (j["verify"], j.get("duty", ""), j.get("duty_evidence", ""), j.get("duty_flag", ""), jid))
        if j.get("sec"):
            con.execute("UPDATE jobs SET sec=? WHERE id=?", (",".join(j["sec"]), jid))
        if j.get("verify_tried_at"):  # 원문 확인 시도일(성공·실패 모두): 실패한 공고는 7일 뒤에 다시 시도
            con.execute("UPDATE jobs SET verify_tried_at=? WHERE id=?", (j["verify_tried_at"], jid))
        if j.get("cmp_salary") or j.get("cmp_size"):
            con.execute("UPDATE jobs SET csn=?, company_form=?, employees=?, avg_salary=?, salary_year=?, cmp_salary=?, cmp_size=? WHERE id=?",
                        (j.get("csn", ""), j.get("company_form", ""), j.get("employees", 0), j.get("avg_salary", 0), j.get("salary_year", 0),
                         j.get("cmp_salary", ""), j.get("cmp_size", ""), jid))
    # 오늘 안 보인 공고: 마감일이 지났거나 7일 넘게 안 보이면 마감 처리 (하루 수집 실패로는 닫지 않음)
    week_ago = (t - dt.timedelta(days=7)).isoformat()
    month_ago = (t - dt.timedelta(days=30)).isoformat()
    # 이번 실행에서 실패한 소스에서만 보던 공고는 '안 보였다'고 할 수 없으므로 7일 규칙에서 뺀다 (30일 넘으면 닫음)
    # '클라우드 알림' = 예전 claude.ai 웹 레이더에서 옮겨 온 공고(tools/import_web_jobs.py): 수집 대상에 없는 회사라 매일 볼 수 없다 → 마감일·30일로만 닫음
    failed = sorted(set(failed_sources) | {"클라우드 알림"})
    fs = f" AND (source NOT IN ({','.join('?' * len(failed))}) OR last_seen<?)" if failed else ""
    con.execute(f"""UPDATE jobs SET status='closed' WHERE status='open' AND last_seen<? AND last_seen<?{fs}""",
                (ts, week_ago, *failed, *([month_ago] if failed else [])))
    con.execute("""UPDATE jobs SET status='closed' WHERE status='open' AND last_seen<? AND
                   deadline GLOB '[0-9][0-9][0-9][0-9]-*' AND deadline<?""", (ts, ts))
    con.execute("""UPDATE jobs SET status='closed' WHERE status='open' AND deadline GLOB '[0-9][0-9][0-9][0-9]-*' AND deadline<?""", (ts,))
    con.commit()
    return new_ids


# ---------------------------------------------------------------- 리포트(HTML)
def build_report(con, new_ids, health, t):
    rows = [dict(zip([c[0] for c in con.execute("SELECT * FROM jobs LIMIT 0").description], r))
            for r in con.execute("SELECT * FROM jobs ORDER BY first_seen DESC")]
    shortcuts = [{"name": s["name"], "url": s["url"], "type": s["type"]} for s in load_json("sources.json") if s["type"] in ("portal", "search")]
    data = {"today": t.isoformat(), "new": new_ids, "jobs": rows, "health": health, "shortcuts": shortcuts,
            "generated": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")}
    tpl = (BASE / "report_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    REPORT.write_text(tpl.replace("/*__DATA__*/null", payload), encoding="utf-8")


# ---------------------------------------------------------------- 알림
def notify(new_jobs, settings):
    n = settings.get("notify", {})
    if not new_jobs:
        return
    title = f"환경안전 채용 신규 {len(new_jobs)}건"
    body = "\n".join(f"· {j['company']} — {j['title'][:40]}" for j in new_jobs[:4])
    if n.get("windows_toast", True) and os.name == "nt":
        try:
            from win11toast import toast
            toast(title, body, on_click=REPORT.as_uri(), duration="long")
        except Exception as e:
            print("  윈도우 알림 실패:", e)
    if n.get("open_report_when_new", True):
        webbrowser.open(REPORT.as_uri())
    em = n.get("email", {})
    if em.get("enabled") and em.get("user") and em.get("password") and em.get("to"):
        try:
            import smtplib
            from email.mime.text import MIMEText
            lines = [f"[{j['track']}] {j['company']} — {j['title']}\n  경력 {j['exp']} · 등록 {j['posted'] or '확인 불가'} · 마감 {j['deadline'] or '확인 불가'}\n  {j['link']}" for j in new_jobs]
            msg = MIMEText("\n\n".join(lines), "plain", "utf-8")
            msg["Subject"], msg["From"], msg["To"] = title, em["user"], em["to"]
            with smtplib.SMTP_SSL(em.get("smtp_host", "smtp.gmail.com"), int(em.get("smtp_port", 465))) as s:
                s.login(em["user"], em["password"])
                s.send_message(msg)
            print("  메일 발송 완료")
        except Exception as e:
            print("  메일 발송 실패:", e)


# ---------------------------------------------------------------- 공고 원문 확인 (정합성)
def verify_against_source(con, jobs, settings, t):
    """사람인 공고(링크에 rec_idx)는 원문을 열어 기간·근무지·경력·근무형태·마감 여부를 공식 값으로 채운다.
    최근 N일 안에 확인한 공고는 radar.db 의 값을 다시 쓰고, 한 번에 max_per_run 건까지만 새로 연다(사람 속도)."""
    import verify_jobs as vj
    cfg = settings.get("verify", {})
    fresh_days, cap = int(cfg.get("recheck_days", 3)), int(cfg.get("max_per_run", 120))
    vj.GAP = float(cfg.get("gap_sec", 4.0))
    since = (t - dt.timedelta(days=fresh_days)).isoformat()
    retry_after = (t - dt.timedelta(days=int(cfg.get("retry_failed_days", 7)))).isoformat()
    opened = 0  # 한도는 '시도' 기준 (실패도 사람인 요청을 쓴다)
    seen = set()
    for j in jobs:
        jid = resolve_id(con, j)
        if jid in seen:
            continue
        seen.add(jid)
        if not re.match(r"https?://", j.get("link", "")):
            continue  # 웹 주소가 아닌 링크(테스트용 file:// 등)는 확인하지 않는다
        row = con.execute("SELECT verified_at, status, verify_tried_at FROM jobs WHERE id=?", (jid,)).fetchone()
        if row and row[0] and row[0] >= since:
            continue  # 최근에 확인함 → DB 값 유지
        if row and row[2] and row[2] >= retry_after and row[2] != row[0]:
            continue  # 최근에 확인을 시도했다가 실패함 → 7일 뒤 재시도
        if opened >= cap:
            continue
        opened += 1
        j["verify_tried_at"] = t.isoformat()
        try:
            d, how = vj.detail_for(j.get("link", ""), j.get("company", ""), j.get("title", ""))
        except Exception as e:
            print(f"  원문 확인 실패: {j['company']} | {str(e)[:80]}")
            continue
        if not d:
            v, ev, ex = vj.duty_check(j.get("title", ""), "")
            j.update(verify=f"원문 확인 필요 ({how})", duty=v, duty_evidence=ev, duty_flag=ex)
            continue
        v, ev, ex = vj.duty_check(" ".join(dict.fromkeys(filter(None, [j.get("title", ""), d.get("title_full", "")]))), d.pop("_body", ""))
        j.update(csn=d.get("csn", ""), v_start=d.get("start", ""), v_deadline=d.get("deadline", ""), v_exp=d.get("exp", ""), location=d.get("location", ""),
                 edu=d.get("edu", ""), emp_type=d.get("emp_type", ""), deadline_time=d.get("deadline_time", ""),
                 company_full=d.get("company_full", ""), verified_at=t.isoformat(), duty=v, duty_evidence=ev, duty_flag=ex,
                 link=d.get("link") or j.get("link", ""),
                 verify=("마감됨 · " if d.get("closed") else "") + f"{d.get('source_site', '')} 원문 확인 {t:%m.%d}" + ("" if how == "링크" else f" ({how})"))
        if v == "의심":  # 본문·제목 어디에도 환경·안전 직무 근거가 없으면 추천에서 내린다 (버리지는 않음)
            j["tier"] = "검토"
        cf = norm_co(d.get("company_full", ""))
        if j.get("check_company") and cf and not (norm_co(j["company"]) in cf or cf in norm_co(j["company"])):
            j["closed"] = True  # '(검색)' 소스에서 온 다른 회사 공고: 원문 회사명이 다르면 넣지 않는다
            j["verify"] = f"다른 회사 공고라 제외 (원문 회사: {d.get('company_full')})"
            print(f"  다른 회사 공고 제외: {j['company']} ≠ {d.get('company_full')} | {j['title'][:40]}")
        if d.get("closed") or (re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.get("deadline", "")) and d["deadline"] < t.isoformat()):
            j["closed"] = True
        time.sleep(vj.GAP)
    print(f"  공고 원문 확인: 새로 {opened}건 (한도 {cap})")


# ---------------------------------------------------------------- 회사 비교 (현재 회사 대비 연봉·규모)
def compare_companies(con, jobs, settings):
    """공고 회사를 settings.baseline(사용자의 현재 회사)과 사람인 기업정보(평균연봉·사원수·기업형태)로 비교.
    회사 정보는 company_cache.json 에 30일 보관 → 새로 묻는 회사만 사람인에 요청(한 번에 max_lookups 곳까지)."""
    import company_info as ci
    cfg = settings.get("baseline", {})
    jobs = [j for j in jobs if re.match(r"https?://", j.get("link", ""))]  # 테스트용 file:// 공고 등은 비교하지 않는다
    if not jobs:
        return
    cache = ci.Cache(str(DATA / "company_cache.json"))
    ci.GAP = float(settings.get("verify", {}).get("gap_sec", 4.0))
    base = {}  # 기준 회사가 없으면(공개 저장소) 연봉·사원수만 모으고 비교는 웹 화면이 각자 기준으로 한다
    if cfg.get("company"):
        try:
            base = ci.profile_for(cfg["company"], cache, cfg.get("csn"))
        except Exception as e:
            print("  기준 회사 정보 실패:", str(e)[:80])
    cap, asked = int(cfg.get("max_lookups", 80)), 0
    for j in jobs:
        if j.get("closed"):
            continue
        name = j.get("company_full") or j["company"]
        row = con.execute("SELECT csn FROM jobs WHERE id=?", (resolve_id(con, j),)).fetchone()
        csn = j.get("csn") or (row[0] if row else "")
        if not cache.get(name):
            if asked >= cap:
                continue
            asked += 1
        try:
            p = ci.profile_for(name, cache, csn or None)
        except Exception as e:
            print(f"  회사 정보 실패: {name} | {str(e)[:60]}"); continue
        if p.get("missing"):
            continue
        sal, size = ci.compare(p, base)
        j.update(csn=p.get("csn", csn), company_form=p.get("form", ""), employees=p.get("employees", 0), avg_salary=p.get("avg_salary", 0),
                 salary_year=p.get("salary_year", 0), cmp_salary=sal, cmp_size=size)
    print(f"  회사 비교(기준 {cfg.get('company') or '없음'} 평균연봉 {base.get('avg_salary', '?')}만원·{base.get('employees', '?')}명): 새로 조회 {asked}곳")


# ---------------------------------------------------------------- 실행 잠금·절전 방지
LOCK = DATA / "run.lock"


def pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(max_age_h=6):
    """같은 데이터 폴더에서 수집이 두 번 겹쳐 돌지 않게 한다. 살아 있는 PID 가 잡고 있으면 False.
    주인이 죽었거나 max_age_h 시간이 지난 잠금은 버린다."""
    for _ in range(2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {dt.datetime.now(KST).isoformat(timespec='seconds')}".encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                pid = int((LOCK.read_text(encoding="utf-8").split() or ["0"])[0])
                age_h = (time.time() - LOCK.stat().st_mtime) / 3600
            except (OSError, ValueError):
                pid, age_h = 0, 0
            if pid_alive(pid) and pid != os.getpid() and age_h < max_age_h:
                print(f"이미 수집이 실행 중입니다 (PID {pid}, 잠금 {LOCK}). 이번 실행은 끝냅니다.")
                return False
            print(f"  오래된 잠금 정리 (PID {pid}, {age_h:.1f}시간)")
            try:
                LOCK.unlink()
            except OSError:
                pass
    return False


def release_lock():
    try:
        if LOCK.exists() and LOCK.read_text(encoding="utf-8").split()[0] == str(os.getpid()):
            LOCK.unlink()
    except (OSError, IndexError):
        pass


def keep_awake(on):
    """수집 중 PC 가 절전으로 들어가 중간에 끊기지 않게 한다 (윈도우 SetThreadExecutionState)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


# ---------------------------------------------------------------- 실행
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--sources", default="sources.json")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--no-verify", action="store_true", help="공고 원문 확인(사람인 상세) 건너뛰기")
    a = ap.parse_args()

    if a.show:
        webbrowser.open(REPORT.as_uri())
        return
    if not acquire_lock():
        sys.exit(3)
    keep_awake(True)
    try:
        run_main(a)
    finally:
        keep_awake(False)
        release_lock()


def run_main(a):
    settings = load_settings()
    fb = list((BASE / "feedback").glob("radar_feedback*.json")) + list((Path.home() / "Downloads").glob("radar_feedback*.json"))
    if fb:
        from relevance import merge_feedback
        n = merge_feedback(fb)
        done = BASE / "feedback" / "반영완료"; done.mkdir(parents=True, exist_ok=True)
        for p in fb:
            try:
                p.replace(done / f"{dt.datetime.now(KST):%Y%m%d%H%M%S}_{p.name}")
            except Exception:
                pass
        print(f"  내 피드백 {n}건 반영 → 모델 재학습")
    sources = [s for s in load_json(a.sources) if a.only in s["name"] and not s.get("disabled")]
    t = today()
    print(f"[{dt.datetime.now(KST):%Y-%m-%d %H:%M}] 소스 {len(sources)}곳 수집 시작")
    results = asyncio.run(crawl(sources, settings, a.debug))

    jobs, health = [], []
    for src, r in zip(sources, results):
        kept, reasons = 0, {}
        for it in r["items"]:
            j, why = judge(src, it, settings, t)
            if j:
                jobs.append(j)
                kept += 1
            else:
                reasons[why] = reasons.get(why, 0) + 1
        suspicious = r["ok"] and r["textLen"] < 300
        health.append({"name": src["name"], "type": src["type"], "url": src["url"], "ok": r["ok"],
                       "error": r["error"], "found": len(r["items"]), "kept": kept,
                       "suspicious": suspicious, "dropped": reasons})

    # 학습 모델로 직무 관련성 점수 → 낮으면 버리고, 높으면 '추천'
    ml = settings.get("ml", {})
    keep_t, rec_t = float(ml.get("keep_threshold", 0.10)), float(ml.get("recommend_threshold", 0.30))
    try:
        from relevance import score
        probs = score(jobs)
    except Exception as e:  # scikit-learn 미설치·DLL 차단(스마트 앱 컨트롤) 시 멈추지 않고 규칙만으로 진행 → 전부 '검토'
        print("  ⚠ 학습 모델을 쓰지 못해 규칙만으로 판정합니다:", type(e).__name__, str(e)[:160])
        probs = [keep_t] * len(jobs)
    scored = []
    for j, p in zip(jobs, probs):
        # 모델은 환경 쪽 라벨로 학습됨 → 안전 분야 공고는 모델 점수로 버리지 않는다(재현율 우선, 등급만 '검토')
        if p < keep_t and "안전" in j.get("sec", []):
            j["sec"] = [s for s in j["sec"] if s == "안전"]
            j["rel_score"], j["tier"] = round(float(p), 3), "검토"
            scored.append(j)
            continue
        if p < keep_t:
            next(h for h in health if h["name"] == j["source"])["dropped"]["모델 점수 낮음"] = \
                next(h for h in health if h["name"] == j["source"])["dropped"].get("모델 점수 낮음", 0) + 1
            next(h for h in health if h["name"] == j["source"])["kept"] -= 1
            continue
        j["rel_score"], j["tier"] = round(float(p), 3), ("추천" if p >= rec_t else "검토")
        scored.append(j)
    jobs = scored

    con = db_open()
    if not a.no_verify and settings.get("verify", {}).get("enabled", True):
        verify_against_source(con, jobs, settings, t)
        compare_companies(con, jobs, settings)
    new_ids = merge(con, jobs, t, failed_sources=[h["name"] for h in health if not h["ok"]])
    open_cnt = con.execute("SELECT COUNT(*) FROM jobs WHERE status='open'").fetchone()[0]
    ok_cnt = sum(h["ok"] and not h["suspicious"] for h in health)
    con.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?)",
                (dt.datetime.now(KST).isoformat(timespec="minutes"), len(new_ids), open_cnt, ok_cnt, len(health),
                 json.dumps(health, ensure_ascii=False)))
    con.commit()
    build_report(con, new_ids, health, t)
    new_jobs = [dict(zip(["company", "title", "track", "exp", "posted", "deadline", "link"], r)) for r in
                con.execute(f"SELECT company,title,track,exp,posted,deadline,link FROM jobs WHERE id IN ({','.join('?' * len(new_ids))})", new_ids)] if new_ids else []
    print(f"완료: 신규 {len(new_ids)}건 · 진행 중 {open_cnt}건 · 정상 소스 {ok_cnt}/{len(health)}")
    print(f"리포트: {REPORT}")
    if not a.no_notify:
        notify(new_jobs, settings)


if __name__ == "__main__":
    main()
