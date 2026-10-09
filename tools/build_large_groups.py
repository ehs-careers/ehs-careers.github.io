"""공정위 기업집단포털(egroup.go.kr)에서 공시대상기업집단 + 상호출자제한기업집단과
전체 소속회사 명단을 받아 data/large_groups.json 을 만든다. (표준 라이브러리만 사용)

출처 화면: 기업집단포털 > 기업집단 현황 > 지정현황 (/egps/ps/io/kap/appnSttusList.do)
  - 탭3 '자산별 지정현황'  : selectAjaxAssetsAppnSttusList.do  (순위·집단명·동일인·계열회사수·자산총액)
      sch_invstmntLmttSeCode=0002 이면 상호출자제한기업집단만
  - 탭4 '소속회사 개요'    : selectAjaxAppnPsitnCmpnySumryList.do (집단명·회사명·업종·종업원수 ...)
  - 조회 년월 목록        : /egps/ps/io/ocm/selectYmList.do

매년 5월 1일 지정 후 다시 실행:
    python tools/build_large_groups.py            # 가장 최근 년월
    python tools/build_large_groups.py --ym 202605
"""
import argparse
import html
import http.cookiejar
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://www.egroup.go.kr"
PAGE = "/egps/ps/io/kap/appnSttusList.do"
YM_LIST = "/egps/ps/io/ocm/selectYmList.do"
GROUPS = "/egps/ps/io/kap/selectAjaxAssetsAppnSttusList.do"
AFFILIATES = "/egps/ps/io/kap/selectAjaxAppnPsitnCmpnySumryList.do"
OUT = Path(__file__).resolve().parent.parent / "data" / "large_groups.json"
DELAY = 2.5  # 요청 간 간격(초)

# 공정위 공식 표기는 영문 약칭을 한글로 적는다(에스케이하이닉스). 공고에는 SK하이닉스로 나오므로 별칭을 만든다.
TRANSLIT = [
    ("에이치디", "HD"), ("에스케이", "SK"), ("엘지", "LG"), ("지에스", "GS"), ("씨제이", "CJ"),
    ("케이티앤지", "KT&G"), ("케이티", "KT"), ("엘에스", "LS"), ("에이치엘", "HL"), ("디엘", "DL"),
    ("디비", "DB"), ("비지에프", "BGF"), ("에스엠", "SM"), ("케이지", "KG"), ("에이치디씨", "HDC"),
    ("오씨아이", "OCI"), ("이앤에이", "E&A"), ("에쓰-오일", "S-OIL"), ("에스케이씨", "SKC"),
]
TRANSLIT.sort(key=lambda p: -len(p[0]))  # 긴 것부터 (에이치디씨 > 에이치디)


def normalize(name: str) -> str:
    """(주)/㈜/주식회사/(유)/유한회사/(사)/공백/가운뎃점 제거, 영문 대문자화."""
    s = name
    s = re.sub(r"\((주|유|합|사|재|유한|합자|합명)\)|㈜|㈐|주식회사|유한책임회사|유한회사|합자회사|합명회사|사단법인|재단법인", "", s)
    s = re.sub(r"[\s·ㆍ.,]", "", s)
    return s.upper()


def aliases(norm: str) -> list:
    out = set()
    for ko, en in TRANSLIT:
        if ko in norm:
            out.add(norm.replace(ko, en))
    # 여러 표기가 섞인 경우(에스케이하이닉스시스템아이씨) 모두 치환한 형태도
    full = norm
    for ko, en in TRANSLIT:
        full = full.replace(ko, en)
    if full != norm:
        out.add(full)
    out.discard(norm)
    return sorted(out)


class Client:
    def __init__(self):
        cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        self.op.addheaders = [("User-Agent", "Mozilla/5.0 (job-radar large_groups builder)"),
                              ("X-Requested-With", "XMLHttpRequest")]
        self.post(PAGE, {}, delay=0)  # 세션 쿠키

    def post(self, path, data, delay=DELAY):
        if delay:
            time.sleep(delay)
        req = urllib.request.Request(BASE + path, data=urllib.parse.urlencode(data).encode())
        with self.op.open(req, timeout=180) as r:
            return r.read().decode("utf-8", "replace")


def cells(row_html):
    return [html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c))).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)]


def rows(page):
    body = page[page.find("<tbody"):] if "<tbody" in page else page
    return [cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)]


def to_int(s):
    s = s.replace(",", "").strip()
    return int(s) if re.fullmatch(r"-?\d+", s) else None


def fetch_groups(cl, ym, code):
    page = cl.post(GROUPS, {"sch_othbcYm": ym, "sch_invstmntLmttSeCode": code, "sch_entrprsClCode": "ALL"})
    out = []
    for c in rows(page):
        if len(c) >= 6 and to_int(c[0]) is not None:
            out.append({"rank": to_int(c[0]), "group": c[1], "owner": c[2],
                        "affiliate_count": to_int(c[3]), "assets_100m_krw": to_int(c[4]), "public_corp": c[5]})
    return out


def fetch_affiliates(cl, ym):
    page = cl.post(AFFILIATES, {"sch_othbcYm": ym, "sch_unityGrupId": "", "pageIndex": "1"})
    groups, cur = {}, None
    for c in rows(page):
        if len(c) == 18:      # 집단의 첫 행: 집단명 칸(rowspan) 포함
            cur, c = c[0], c[1:]
        if len(c) != 17 or cur is None:
            continue
        groups.setdefault(cur, []).append({"name": c[0], "industry": c[5], "employees": to_int(c[6])})
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ym", help="조회 년월 YYYYMM (기본: 포털에 있는 가장 최근)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()

    cl = Client()
    yms = [x["id"] for x in json.loads(cl.post(YM_LIST, {}))["resultList"]]
    if a.ym and a.ym not in yms:
        sys.exit(f"{a.ym} 없음. 가능한 년월: {yms[:8]} ...")
    # 최신 년월이라도 자료가 비어 있을 수 있음(2026-10 실측: 202606 은 0건) → 자료 있는 가장 최근 년월
    all_groups = []
    for ym in ([a.ym] if a.ym else sorted(yms, reverse=True)[:6]):
        all_groups = fetch_groups(cl, ym, "ALL")
        print("조회 년월", ym, "집단", len(all_groups), file=sys.stderr)
        if all_groups:
            break
    if not all_groups:
        sys.exit("기업집단 자료를 찾지 못함 — 포털 구조 변경 여부 확인")

    sangchul = {g["group"] for g in fetch_groups(cl, ym, "0002")}
    aff = fetch_affiliates(cl, ym)

    result, total = [], 0
    for g in all_groups:
        lst = aff.get(g["group"], [])
        total += len(lst)
        if g["affiliate_count"] is not None and g["affiliate_count"] != len(lst):
            print(f"경고: {g['group']} 계열회사수 {g['affiliate_count']} != 명단 {len(lst)}", file=sys.stderr)
        result.append({
            "group": g["group"],
            "group_aliases": aliases(normalize(g["group"])),
            "rank": g["rank"],
            "type": "상호출자제한기업집단" if g["group"] in sangchul else "공시대상기업집단",
            "owner": g["owner"],
            "affiliate_count": g["affiliate_count"],
            "assets_100m_krw": g["assets_100m_krw"],
            "public_corp": g["public_corp"],
            "affiliates": [x["name"] for x in lst],
            "affiliates_detail": [dict(x, norm=normalize(x["name"]), aliases=aliases(normalize(x["name"])))
                                  for x in lst],
        })
    missing = set(aff) - {g["group"] for g in all_groups}
    if missing:
        print("경고: 순위표에 없는 집단", missing, file=sys.stderr)

    doc = {
        "source": f"공정거래위원회 기업집단포털 {BASE}{PAGE} (지정현황 > 자산별 지정현황 / 소속회사 개요), 조회 년월 {ym}",
        "ym": ym,
        "year": int(ym[:4]),
        "designation_note": f"{ym[:4]}년 5월 1일 지정 기준(포털 조회 년월 {ym}). 지정 이후 계열 편입·제외는 해당 월 자료에만 반영됨.",
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "group_count": len(result),
        "sangchul_group_count": len(sangchul),
        "affiliate_count": total,
        "fields": {"assets_100m_krw": "자산총액(억원)", "affiliates_detail.norm": "(주)/㈜/주식회사/공백 제거",
                   "aliases": "에스케이→SK 등 한글 표기 영문 약칭 치환"},
        "groups": result,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{a.out}: 집단 {len(result)} (상출 {len(sangchul)}) · 소속회사 {total}", file=sys.stderr)


if __name__ == "__main__":
    main()
