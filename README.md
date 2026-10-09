# 환경·안전 채용 공고 모음

국내 중견 이상 기업의 **환경·안전 직무** 채용 공고를 매일 자동으로 모아 한 화면에 보여 줍니다.

- **보는 법**: 공유받은 링크(끝에 `#k=…`)를 한 번 열면 끝. 설치·로그인 없음, 폰에서도 열림. 이후엔 그 브라우저에서 주소만으로 열림.
  - 공고 데이터는 암호문(`site/data/jobs.enc`, `state/radar.db.enc`)으로만 저장·게시된다. 열쇠는 링크의 `#` 뒤에만 있고 서버로 전송되지 않는다 → 링크를 받은 사람만 볼 수 있음.
  - 환경 탭부터: `?p=env` · 안전 탭부터: `?p=safety`
- **갱신**: 매일 08:50·18:50(한국 시간) GitHub 서버가 회사 채용 페이지 약 350곳을 읽고, 어제 목록과 비교해 새 공고를 표시합니다. PC 를 켜 둘 필요 없음.
- **내 표시**(관심·지원·패스·메모·자기소개서)는 각자 브라우저에만 저장됩니다. 가끔 `사이트 바로가기 → 내 설정 → 백업 내보내기`.

## 분야 기준 (`settings.json` → `profiles`)
| 분야 | 들어오는 것 | 빼는 것 |
|---|---|---|
| 환경 | 환경·화학물질(화관법)·대기·수질·폐기물·온실가스·EHS 통합 직무 | 측정·분석 대행, 컨설팅, 환경설비·수처리 엔지니어, 건설현장 |
| 안전 | 산업안전·공정안전(PSM)·안전보건·산업위생/보건관리자·EHS 통합 직무 | 간호사 보건관리자, 소방·방재, 전기·가스 안전관리자, 건설 **현장** 채용 (대기업 건설사 본사·사무직은 포함) |

'환경안전'처럼 둘 다 해당하면 두 탭에 모두 보입니다. 공통: 경력 하한 7년 이하, 신입은 대기업만.

## 구성
- `job_radar.py` 수집·판정 → `state/radar.db`(어제 목록) · `tools/export_site.py` → `site/data/jobs.json` → `site/index.html`(웹 페이지)
- `verify_jobs.py` 공고 원문 확인(마감일·근무형태·직무) · `company_info.py` 회사 연봉·규모(사람인 기업정보)
- `ml/relevance.py` 규칙 + 학습 모델(`ml/labeled_data.json` 1,190건) · `sources.json` 수집 대상
- `.github/workflows/collect.yml` 매일 실행 · 실패하면 저장소 주인에게 GitHub 메일

## 넘겨받아 직접 운영하려면
이 저장소를 **Fork** → `Settings → Secrets and variables → Actions` 에 `RADAR_KEY`(아무 긴 무작위 문자열) 추가 → `Settings → Pages → Source: GitHub Actions` → `Actions` 탭에서 workflow 사용 허용.
첫 실행은 빈 목록에서 시작합니다(기존 암호문 `state/radar.db.enc`·`site/data/jobs.enc` 는 지우고 시작). 링크는 `https://<계정>.github.io/<저장소>/#k=<RADAR_KEY>`.
분야 기준·대상 회사를 바꾸려면 `settings.json`(profiles) · `sources.json` 을 고치고 `python tests/run_tests.py` 가 PASS 인지 확인하세요.
