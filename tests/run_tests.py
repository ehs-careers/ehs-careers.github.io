# -*- coding: utf-8 -*-
"""회귀 테스트: 가짜 채용 페이지 3종(정적 목록 / JS로 그려지는 포털 / 여러 회사가 섞인 검색)에서
들어와야 할 공고는 들어오고, 빠져야 할 공고는 빠지는지 확인한다. 수집 로직을 고친 뒤 반드시 실행.
사용: python tests/run_tests.py   (통과 시 exit 0)"""
import json, os, shutil, sqlite3, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FX = HERE / "fixtures"

MUST_KEEP = {
    "2026년 사무직 신입사원 채용 (안전환경)",      # 대기업 신입·안전환경
    "공정안전(PSM) 담당 경력사원",                 # 경력 3년↑
    "울산 환경안전 경력 채용",                      # JS 포털, 2~7년
    "EHS 신입사원 채용",                            # JS 포털, 대기업 신입
    "울산공장 환경안전 담당자 모집",                # 검색결과, 외국계 대기업
    "EHS 엔지니어",                                 # 검색결과, 중견
    "Safety Engineer (울산공장)",                   # 영어 표기
}
MUST_DROP = {
    "환경안전팀장",                                   # 경력 12년↑
    "울산공장 상주 협력사 안전관리자",               # 협력사 상주
    "환경안전 신입 채용",                            # 작은 회사 신입
    "환경안전 경력직",                               # 마감 지난 공고
    "코오롱글로벌(주) 군산 공동주택 현장 소방안전관리자 채용",  # 건설현장 소방
    "배터리 생산기술 신입",                          # 직무 무관
    "2026년 사무직 신입사원 채용 (재무)",           # 직무 무관
}


# 회사명 판정: (블록, 기대 회사, 기대 대기업 여부). 2026-10-04 실제 오분류(HLB제넥스→HL, '(삼성內)'→삼성)에서 만든 사례
COMPANY_CASES = [
    ("HLB제넥스 환경안전보건(ESH팀) 신규 직원 채용", "", False),
    ("반도체 장비 환경안전(EHS) 담당자 채용(삼성內)", "", False),
    ("SK이노베이션 협력사 환경안전 담당 모집", "", False),
    ("GSK 환경안전 담당", "", False),
    ("SK에너지 울산CLX 환경안전 경력", "SK에너지", True),
    ("LG화학 여수공장 PSM 경력", "LG화학", True),
    ("3M EHS Specialist", "3M", True),
    ("HL만도 환경안전", "HL", True),
    ("[대주KC그룹] 케이씨 환경안전팀 통합환경관리자", "케이씨", False),
]


def check_company():
    sys.path.insert(0, str(ROOT))
    import job_radar as jr
    st = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    bad = []
    for block, co, large in COMPANY_CASES:
        got = jr.find_company(block, st)
        if got != co or (bool(got) and jr.is_large(got, st)) != large:
            bad.append(f"{block} → {got!r} (기대 {co!r}, 대기업={large})")
    return bad


# 공고 원문(사람인 상세) 경력 값 추출: '학력' 바로 앞의 값만, 머리글의 '경력 채용' 등은 제외
EXP_CASES = {"채용 8 D-6 홈페이지 지원 핵심 정보 경력 경력 5~10년 학력 대졸": "경력 5~10년", "경력 신입 학력 대졸": "신입", "경력 경력 1년 ↑ 학력 고졸": "경력 1년 ↑",
             "경력 신입·경력 학력 무관": "신입·경력", "경력 경력무관 학력 무관": "경력무관", "경력 신입 · 경력 3년 ↑ 학력 무관": "신입 · 경력 3년 ↑",
             "경력 경력 학력 대졸": "경력", "경력 무관 학력 무관": "무관", "경력 3~7년 학력 대졸": "경력 3~7년"}


def check_exp():
    sys.path.insert(0, str(ROOT))
    import verify_jobs as vj
    return [f"{t} → {vj.exp_of(t)!r} (기대 {w!r})" for t, w in EXP_CASES.items() if vj.exp_of(t) != w]


# 공고 본문 직무 판정: (제목, 본문, 기대 판정, 제외 신호에 들어 있어야 할 글자)
DUTY_CASES = [
    ("[OCI] 광양공장 엔지니어 경력직", "모집분야 환경 엔지니어\n담당업무 대기·수질 인허가 및 배출시설 관리", "확인", ""),
    ("각 부문 신입/경력 채용", "모집분야\n(마감)화학물질관리, 인사 Payroll, 생산엔지니어", "의심", "마감"),
    ("안전관리자 경력 채용", "", "제목만", ""),
    ("각 부문 경력 채용", "모집분야 생산 엔지니어, 회계, 구매", "의심", ""),
    ("현장 안전관리자 채용", "담당업무\n아파트 건설현장 안전관리 및 공정 점검", "의심", "건설"),  # 건설현장 안전관리자 = 제외 유형
    ("HSE Manager 경력직 채용", "담당업무\nHSE 시스템 운영 및 법규 대응\n회사소개 영업 네트워크 전국 30개", "확인", ""),  # 본문 다른 곳 '영업' 은 경고 아님
    ("ESG 환경(E) 담당 경력사원 채용 2026/09/29 ~ 2026/10/12", "", "제목만", ""),  # 실제 사례(효성)
    ("환경", "", "제목만", ""),  # 실제 사례(금호석유화학 포털 직무 탭)
    ("부문별 신입/경력사원 수시채용 (안전)", "", "제목만", ""),  # 실제 사례(KCC글라스)
    ("2026년 하반기 대졸 신입 및 경력사원 채용 (환경)", "", "제목만", ""),  # 실제 사례(팜한농)
    ("[앰코코리아] 신입, 경력 사원 수시 채용", "", "의심", ""),  # 근거 없음 → 의심 유지
    # 실제 사례(영풍, 잡코리아): 한 줄로 이어진 본문 + 사이트 문구 + 마감 분야와 열린 분야가 섞임
    ("각 부문 신입/경력 정규직 채용", "채용정보에 잘못된 내용이 있을 경우 문의 해주세요. 모집요강 모집분야 공정시설 엔지니어, (마감)생산엔지니어, 인사(Payroll), (마감)화학물질관리, 토양/지하수 정화관리, 지속가능경영(ESG)", "확인", "마감"),
]


# 공고 후보가 실제 공고인지 (2026-10-04 실제 수집 결과에서 뽑은 사례): (title, href, block, 남아야 하는지)
POSTING_CASES = [
    ("우대자격증 : 화공기사, 산업안전기사", "", "우대자격증 : 화공기사, 산업안전기사", False),
    ("산업안전보건법 관련 업무", "", "산업안전보건법 관련 업무", False),
    ("환경", "", "환경담당업무환경 및 화학물질 관련 법규 이행공정 개선 검토, 환경목표 및 실적 관리회사/근무지금호석유화학 / 여수관련전공환경공학 / 화학공학학력학사우대사항필수자격증 : 대기환경기사", True),
    ("안전관리자", "https://www.saramin.co.kr/zf_user/jobs/list/job-category?cat_kewd=2037", "안전관리자", False),
    ("유한환경산업(주)", "https://www.saramin.co.kr/zf_user/company-info/view?csn=LzBV", "유한환경산업(주) 관심기업 등록", False),
    ("안전관리자 신규 채용", "https://www.saramin.co.kr/zf_user/jobs/relay/view?view_type=search&rec_idx=55100290", "[안전관리자] 안전관리자 신규 채용", True),
    ("EHS 신입사원 채용", "", "EHS 신입사원 채용 대기업 울산", True),
    # 그룹 채용 포털의 홍보 문구 링크
    ("친환경소재의 대중화를 꿈꾸는 당신의 동료 정용복 책임", "https://careers.lg.com/people/123", "친환경소재의 대중화를 꿈꾸는 당신의 동료 정용복 책임", False),
    ("근무환경소개", "https://ecoprorecruit.co.kr/env", "근무환경소개", False),
    ("ESG 환경(E) 담당 경력사원 채용", "https://hyosung.recruiter.co.kr/job/1", "ESG 환경(E) 담당 경력사원 채용 2026/09/29", True),
]


def check_posting():
    import job_radar as jr
    bad = []
    for ti, href, block, keep in POSTING_CASES:
        why = jr.not_a_posting({"title": ti, "href": href, "block": block}, {})
        if (why == "") != keep:
            bad.append(f"공고 판정 {ti!r} → {why or '공고'} (기대 {'공고' if keep else '공고 아님'})")
    return bad


def check_duty():
    import verify_jobs as vj
    bad = []
    for ti, body, want, flag in DUTY_CASES:
        v, ev, ex = vj.duty_check(ti, body)
        if v != want or (flag and flag not in ex):
            bad.append(f"직무 판정 {ti!r} → {v}/{ex!r} (기대 {want}/{flag!r})")
    return bad


# 마감일 읽기 (2026-10-08 실제 오류에서 만든 사례): (블록, 오늘, 기대값)
DEADLINE_CASES = [
    ("접수기간 2026.10.01 (수) 09:00 ~ 2026.10.15 18:00", "2026-10-08", "2026-10-15"),  # 기간이면 끝 날짜 (시작일을 마감으로 읽어 '마감 지남' 처리되던 문제)
    ("2026.10.01(수) ~ 2026.10.15(목)", "2026-10-08", "2026-10-15"),
    ("09/29 ~ 10/12", "2026-10-08", "2026-10-12"),
    ("2026.10.20(화) 마감", "2026-10-08", "2026-10-20"),
    ("채용시스템에서 지원 ~10.20", "2026-10-08", "2026-10-20"),  # '채용시스템' ≠ 채용시 마감
    ("채용시작 2026.10.01 ~ 2026.10.31", "2026-10-08", "2026-10-31"),
    ("채용시 마감", "2026-10-08", "상시"),
    ("채용시까지", "2026-10-08", "상시"),
    ("~ 03.15", "2026-10-08", "2027-03-15"),   # 해 넘김: 10월에 3월 → 내년
    ("~ 12.20", "2026-02-10", "2025-12-20"),   # 2월에 12월 → 작년
    ("~ 11.30", "2026-10-08", "2026-11-30"),
    ("경력 2~7년 D-5", "2026-10-08", "2026-10-13"),  # 경력 범위를 날짜로 읽지 않음
    ("접수기간: 2026년 10월 03일(토) ~ 2026년 10월 12일(월) | 남은일: 4", "2026-10-08", "2026-10-12"),  # SK 채용 API 표기
    ("모집기간 10월 1일 ~ 10월 20일", "2026-10-08", "2026-10-20"),
    ("근무 6개월 이상", "2026-10-08", ""),
]

CANON_CASES = [
    ("https://www.saramin.co.kr/zf_user/jobs/relay/view?view_type=search&rec_idx=55100290&t_ref=x", "saramin:55100290"),
    ("https://www.saramin.co.kr/zf_user/search?searchword=환경안전", ""),
    ("https://www.jobkorea.co.kr/Recruit/GI_Read/48012345?Oem_Code=C1", "jobkorea:48012345"),
    ("https://job.incruit.com/jobdb_info/jobpost.asp?job=2510080001234", "incruit:2510080001234"),
    ("https://careers.lg.com/apply/123", ""),
]


def check_parsing():
    import datetime as dt
    import job_radar as jr
    bad = []
    for block, today, want in DEADLINE_CASES:
        got = jr.parse_deadline(block, dt.date.fromisoformat(today))
        if got != want:
            bad.append(f"마감일 {block!r} (오늘 {today}) → {got!r} (기대 {want!r})")
    for link, want in CANON_CASES:
        if jr.canon_key(link) != want:
            bad.append(f"공고 번호 {link} → {jr.canon_key(link)!r} (기대 {want!r})")
    return bad


def check_merge():
    """merge 규칙: 같은 공고 번호는 기존 행에 합침 / 원문 확인 값은 화면 값으로 안 덮음 / 원문상 마감은 다시 안 엶 /
    실패한 소스의 공고는 7일 규칙으로 안 닫음 (2026-10-08)"""
    import datetime as dt
    import job_radar as jr
    old_path = jr.DB_PATH
    jr.DB_PATH = ":memory:"
    try:
        con = jr.db_open()
    finally:
        jr.DB_PATH = old_path
    t = dt.date(2026, 10, 8)
    old = "2026-09-28"
    ver_id = jr.job_id({"company": "LG화학", "title": "PSM 경력"})  # 링크에 공고 번호가 없으면 회사|제목 해시로 찾는다
    cols = "id,company,title,track,exp,posted,deadline,link,source,snippet,first_seen,last_seen,status,verify,verified_at"
    rows = [
        ("j-dup", "S-OIL", "환경안전 경력", "경력", "3년", "", "2026-10-30", "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=111", "A", "", old, old, "open", "", ""),
        (ver_id, "LG화학", "PSM 경력", "경력", "경력 3~7년", "2026-10-01", "2026-10-20", "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=222", "A", "", old, old, "open", "사람인 원문 확인", "2026-10-07"),
        ("j-cls", "SK에너지", "안전 경력", "경력", "", "", "", "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=333", "A", "", old, old, "closed", "마감됨 · 사람인 원문 확인", "2026-10-07"),
        ("j-fail", "GS칼텍스", "환경 경력", "경력", "", "", "상시", "https://x.example/1", "실패소스", "", old, old, "open", "", ""),
        ("j-gone", "한화솔루션", "환경 경력", "경력", "", "", "상시", "https://x.example/2", "정상소스", "", old, old, "open", "", ""),
    ]
    con.executemany(f"INSERT INTO jobs({cols}) VALUES({','.join('?' * 15)})", rows)
    base = dict(track="경력", snippet="", size_rank=1, size="대기업", rel_score=0.5, tier="추천", posted="", source="A")
    jobs = [
        dict(base, company="S-OIL", title="[S-OIL] 환경안전 경력직 채용", exp="경력", deadline="", link="https://www.saramin.co.kr/zf_user/jobs/relay/view?view_type=list&rec_idx=111"),
        dict(base, company="LG화학", title="PSM 경력", exp="경력", deadline="2026-10-09", link="https://www.saramin.co.kr/zf_user/search?searchword=x"),
        dict(base, company="SK에너지", title="안전 경력", exp="경력", deadline="", link="https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=333"),
    ]
    new_ids = jr.merge(con, jobs, t, failed_sources=["실패소스"])
    got = {r[0]: r[1:] for r in con.execute("SELECT id, status, link, deadline, exp FROM jobs")}
    bad = []
    if new_ids or len(got) != 5:
        bad.append(f"같은 공고 번호인데 새 행이 생김: {new_ids}")
    if got["j-dup"][0] != "open" or "rec_idx=111" not in got["j-dup"][1]:
        bad.append(f"공고 번호로 기존 행 갱신 안 됨: {got['j-dup']}")
    got["j-ver"] = got.pop(ver_id)
    if got["j-ver"][1:] != ("https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=222", "2026-10-20", "경력 3~7년"):
        bad.append(f"원문 확인 값이 화면 값으로 덮임: {got['j-ver']}")
    if got["j-cls"][0] != "closed":
        bad.append("원문상 마감 공고가 다시 열림")
    if got["j-fail"][0] != "open":
        bad.append("실패한 소스의 공고가 7일 규칙으로 닫힘")
    if got["j-gone"][0] != "closed":
        bad.append("정상 소스에서 7일 넘게 안 보인 공고가 안 닫힘")
    con.close()
    return bad


# 분야 판정 (docs/profiles_spec.md 의 사용자 원문 기준): (제목, 회사, 규모 등급, 기대 분야)
SECTION_CASES = [
    ("환경안전팀 경력 채용", "(주)LG화학", 1, ["환경", "안전"]),
    ("화학물질관리 담당", "한화솔루션", 1, ["환경"]),
    ("PSM 공정안전 담당", "S-OIL", 1, ["안전"]),
    ("보건관리자(산업위생) 채용", "삼성전자", 1, ["안전"]),
    ("보건관리자(간호사) 채용", "삼성전자", 1, []),            # 간호사 제외
    ("소방안전관리자", "롯데케미칼", 1, []),                    # 소방 제외
    ("안전관리자 채용(본사)", "현대건설", 1, ["안전"]),          # 대기업 건설사 사무직
    ("안전관리자 채용(본사)", "○○건설", 5, []),                 # 대기업 아닌 건설사
    ("○○아파트 신축공사 현장 안전관리자", "현대건설", 1, []),   # 현장 채용
    ("EHS Specialist (Environment)", "Dow", 2, ["환경", "안전"]),
    ("전기 안전관리자 채용 공고", "롯데엔지니어링플라스틱", 1, []),  # 전기안전관리자(전기 면허 직무)는 산업안전 아님
    # 2026-10-08 사람인·잡코리아 대기업 필터 검색에서 실제로 섞여 들어온 다른 직무
    ("산업 위생 담당", "LG화학", 1, ["안전"]),                   # '위생' 제외가 산업위생을 막으면 안 됨
    ("식품위생안전 담당자", "세스코", 4, []),
    ("열차안전원 채용", "김포골드라인", 4, []),
    ("안전보조원(안전지킴이) 채용", "현대건설", 1, []),
    ("자동차 안전부품 품질관리", "아시모리코리아", 4, []),
    # 2026-10-09 클라우드 첫 수집에서 섞인 다른 직무
    ("안전유리 수주/판촉 직무 신입 채용", "KCC글라스", 1, []),
    ("2026 4분기 신입사원 채용 | (코딩) Frontend Developer_안전 통합 시스템 개발", "현대오토에버", 1, []),
    ("LG이노텍 멕시코 법인 안전환경 분야 경력사원 채용", "LG이노텍", 1, []),
    ("2026년 하반기 계약사원 채용 | 보건관리자", "KT&G", 1, []),
    ("부문별 신입/경력사원 수시채용 (안전)", "KCC글라스", 1, ["안전"]),  # Environment 의 'rn' 이 간호(RN) 제외에 걸리면 안 됨
]


# 회사 등급(사용자 지시 2026-10-08 "어중간한 중견은 빠지고 대기업 위주"): (행, 규모 글자, 기대 등급)
TIER_CASES = [
    ({"company": "LG화학", "size_rank": 1}, "대기업", "대기업"),
    ({"company": "일진에너지", "size_rank": 4, "employees": 300, "avg_salary": 6463}, "중견", "중견·기타"),
    ({"company": "케이씨", "size_rank": 4, "employees": 1200, "avg_salary": 5000}, "중견", "우량 중견"),
    ({"company": "OO화학", "size_rank": 4, "employees": 400, "avg_salary": 7500}, "중견", "우량 중견"),
    ({"company": "OO소재", "size_rank": 4, "employees": 17, "avg_salary": 8519}, "중견", "중견·기타"),  # 작은 회사 연봉 추정치만으로는 안 됨
    ({"company": "OO화학", "size_rank": 4}, "중견", "우량 중견"),            # 회사 정보 미확인이라도 '중견 목록'(mid_companies) 회사는 일단 보임
    ({"company": "XX물산", "size_rank": 4}, "중견", "중견·기타"),            # 목록에 없고 정보도 없으면 숨김 (모음 검색으로 들어온 회사)
    ({"company": "(주)맨파워코리아", "title": "산업안전관리 (물류센터)", "size_rank": 4}, "중견", "중견·기타"),  # 파견·인력 회사
    ({"company": "㈜피플케어코리아", "title": "EHS (안전관리 수석급) - 최고 대기업", "size_rank": 4}, "중견", "대기업"),  # 헤드헌팅인데 고객사가 대기업
    ({"company": "OO산업", "size_rank": 5}, "기타", "중견·기타"),
    ({"company": "에이치알그룹 → 대기업 본사", "title": "EHS 과차장급", "size_rank": 6}, "헤드헌팅(고객사 비공개)", "대기업"),
    ({"company": "엘림써치 → 용접재료 중견기업", "title": "환경안전팀장", "size_rank": 6}, "헤드헌팅(고객사 비공개)", "중견·기타"),
]


def check_tiers():
    sys.path.insert(0, str(ROOT / "tools"))
    import export_site as ex
    mids = ("OO화학",)
    return [f"등급 {r['company']} → {ex.co_tier(r, s, {}, mids)[0]} (기대 {w})" for r, s, w in TIER_CASES if ex.co_tier(r, s, {}, mids)[0] != w]


# 화면용 중복 합치기 (2026-10-09 실제 사례): (공고 A, 공고 B, 합쳐야 하나)
DUP_CASES = [
    (("에이치디현대삼호㈜", "크레인 안전관리 경력사원 채용"), ("HD현대삼호", "크레인 안전관리 경력사원 채용"), True),
    (("쿠팡풀필먼트서비스(유)", "물류센터 EHS(안전, 보건, 소방, 환경)"), ("쿠팡풀필먼트서비스(쿠팡CFS)", "[쿠팡CFS] 물류센터 EHS(안전, 보건, 소방, 환경)"), True),
    (("㈜글로벌스카우트", "반도채 소재 대기업 계열사 가스 안전관리자 경력직 채용중"), ("㈜글로벌스카우트", "반도채 소재 대기업 계열사 가스 안전관리자 경력직 채용"), True),
    (("엘에스엠앤엠", "각 부문별 상시채용(안전관리)"), ("엘에스엠앤엠", "각 부문별 상시채용(보건관리)"), False),   # 다른 분야 = 다른 공고
    (("LS전선", "환경안전 경력직 채용 공고"), ("(주)세종전선", "[세종전선(LS전선 관계사)] 환경안전 경력직 채용 공고"), False),  # 다른 회사
    (("DS단석", "DS단석 평택공장 친환경에너지 바이오디젤 공무팀(기계) 채용"), ("DS단석", "DS단석 평택공장 친환경에너지 바이오디젤 생산직 채용"), False),
    # 2026-10-09 2차: 순서·날짜·칸막이만 다른 같은 공고 / 지역·분야·직급만 다른 다른 공고
    (("S-OIL", "2026년 사무직 신입사원 채용 | 안전환경"), ("S-OIL", "2026년 S-OIL 사무직 신입사원 채용 (안전환경)"), True),
    (("콜마비앤에이치", "콜마비앤에이치 환경안전 신입/경력 채용(음성)"), ("콜마비앤에이치", "콜마비앤에이치㈜ 음성공장 환경안전 신입/경력 채용"), True),
    (("효성티앤씨㈜", "ESG 환경(E) 담당 경력사원 채용"), ("효성", "ESG 환경(E) 담당 경력사원 채용 2026/09/29 ~ 2026"), True),
    (("엘앤피자동차코리아유한책임회사", "인사총무(EHS·총무·IT 지원)"), ("엘앤피자동차코리아 유한책임회사", "인사총무사무원 (EHS·총무·IT 지원)"), True),
    (("퍼솔코리아(유)", "안전관리자 (신입~경력) / 창원 근무"), ("퍼솔코리아(유)", "안전관리자 (신입~경력) / 천안 근무"), False),
    (("신라에이치엠㈜", "본사(HQ) 안전환경그룹 인재 채용"), ("신라에이치엠㈜", "본사(HQ) 안전환경그룹 그룹장 채용"), False),
    (("㈜피플케어코리아", "EHS (환경관리 수석급) - 최고 대기업"), ("㈜피플케어코리아", "EHS (안전관리 수석급) - 최고 대기업"), False),
]


def check_dups():
    sys.path.insert(0, str(ROOT / "tools"))
    import dedup_view as dv
    bad = []
    for (ca, ta), (cb, tb), want in DUP_CASES:
        jobs = [{"id": "a", "company": ca, "title": ta, "status": "open", "sec": ["안전"], "firstSeen": "2026-10-08", "link": "", "deadline": ""},
                {"id": "b", "company": cb, "title": tb, "status": "open", "sec": ["안전"], "firstSeen": "2026-10-08", "link": "", "deadline": ""}]
        got = len(dv.merge_duplicates(jobs)[0]) == 1
        if got != want:
            bad.append(f"중복 판정 {ca}/{ta} ↔ {cb}/{tb} → {got} (기대 {want})")
    return bad


def check_sections():
    sys.path.insert(0, str(ROOT / "ml"))
    import relevance as R
    st = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    return [f"분야 {t} / {c} → {R.sections({'title': t, 'company': c}, st, rk)} (기대 {w})"
            for t, c, rk, w in SECTION_CASES if R.sections({"title": t, "company": c}, st, rk) != w]


def main():
    bad_co = check_company() + check_exp() + check_duty() + check_posting() + check_parsing() + check_merge() + check_sections() + check_tiers() + check_dups()
    for b in bad_co: print("  ✗ 회사명 판정:", b)
    tmp = Path(tempfile.mkdtemp(prefix="jobradar_test_"))
    src = [
        {"name": "테스트 S-OIL", "type": "company", "url": (FX / "company.html").as_uri(), "company": "S-OIL"},
        {"name": "테스트 SK 포털", "type": "portal", "url": (FX / "portal.html").as_uri(), "company": "SK"},
        {"name": "테스트 검색", "type": "search", "url": (FX / "search.html").as_uri()},
    ]
    sp = tmp / "sources_test.json"
    sp.write_text(json.dumps(src, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, JOBRADAR_DATA=str(tmp / "data"), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "job_radar.py"), "--sources", str(sp), "--no-notify"],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:]); print("FAIL: 수집기 실행 오류"); sys.exit(1)
    con = sqlite3.connect(tmp / "data" / "radar.db")
    got = {t for (t,) in con.execute("select title from jobs where status='open'")}
    con.close()
    miss = sorted(t for t in MUST_KEEP if t not in got)
    wrong = sorted(t for t in MUST_DROP if t in got)
    print(f"수집된 공고 {len(got)}건")
    for t in miss: print("  ✗ 들어와야 하는데 빠짐:", t)
    for t in wrong: print("  ✗ 빠져야 하는데 들어옴:", t)
    shutil.rmtree(tmp, ignore_errors=True)
    if miss or wrong or bad_co:
        print("FAIL"); sys.exit(1)
    print("PASS: 회귀 테스트 통과")


if __name__ == "__main__":
    main()
