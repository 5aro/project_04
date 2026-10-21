# IdeaLens — 아이디어 구체화와 특허·사업성 사전 검토

4인·7일 미니 프로젝트입니다. 사용자가 아이디어를 한 번 입력하면 핵심 항목 초안을 자동 작성하고, 사용자가 확인·수정한 뒤 다음 흐름으로 분석합니다.

```text
아이디어 입력 → 해결 과제·핵심 기술·대상 고객·차별점·수익 모델 자동 작성/수정
→ KIPRIS 특허 후보 검색 → 임베딩 검색으로 최대 5건 선정
→ 특허별 데이터화·기술 구성 비교 → 시장 근거 조사
→ 사실·가설·추가 검증 항목을 구분한 사업기획 보고서 초안
```

결과 화면은 아이디어 요약 표, 클릭 가능한 관련 특허 원형 지도, 한국 출원 전 확인 체크리스트, 출처별 시장 근거, 근거 상한이 적용된 사업성 검토, 편집·미리보기 가능한 사업기획 보고서 초안, 다음 검증 과제로 구성합니다. 보고서는 특허·시장 근거와 가정, 공백, 검증 계획을 모아 Markdown으로 내려받을 수 있습니다. 최근 검토 기록은 현재 브라우저 세션에만 유지됩니다.

발표용 서비스 흐름, 보고서 구성, 사업성 데이터 기준, 3분 시연 순서는 [발표용 워크플로우](docs/PRESENTATION_WORKFLOW.md)를 참고하세요. 4인 역할·7일 일정과 채택 검증 가설은 [팀 계획](docs/TEAM_PLAN.md)에 있습니다.

## 로컬 실행

Python 3.12 권장.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

기존 `.env`에서 `IDEALENS_MODE=rag`, `OPENAI_API_KEY`, KIPRIS Plus 키를 설정합니다. 기본 KIPRIS 프로필은 `access_key`이며 키가 `ServiceKey` 형식이면 `KIPRIS_API_PROFILE=service_key`로 바꿉니다. API 키를 채팅이나 저장소에 넣지 마세요.

터미널 1:

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

터미널 2:

```bash
source .venv/bin/activate
streamlit run frontend/app.py --server.address 127.0.0.1
```

### 오프라인 특허 색인 구축

분석 서버를 시작하기 전에 기존 `.env`에 KIPRIS Plus와 OpenAI 키를 설정한 후 관심 분야별로 실행합니다.

```bash
python indexing.py --query "전기차 급속충전" --query "배터리 열관리" --query "충전 전력 제어"
```

검색어 파일도 지원합니다 (`--query-file data/seed_queries.txt`). 기본 저장 위치는 `data/chroma/`, 기본 컬렉션은 `idealens_patents_v1`입니다. 재색인은 기존 문서를 유지하면서 같은 특허의 내용을 갱신하고 새 특허를 추가합니다. 이후 `.env`에서 `PATENT_SOURCE=chroma`로 설정하고 FastAPI를 재시작하면 됩니다. 색인 범위·검색어·갱신 시각·청구항 포함 건수는 앱 상단에서 확인할 수 있습니다.

화면은 http://localhost:8501, API 문서는 http://localhost:8000/docs 입니다. 화면 상단에서 모드와 검색 범위를 확인할 수 있습니다.

### 데모 모드

`IDEALENS_MODE=demo`는 가상 특허 5건, 해시 기반 벡터 검색, 고정 응답을 사용합니다. 실제 특허·시장 검색을 수행하지 않고 사업성 점수를 보류합니다.

### 실제 특허·시장 분석

- `PATENT_SOURCE=kipris`는 KIPRIS Plus 검색 API에서 파서가 만든 질의 최대 3개로 후보를 받고, 출원번호 중복 제거와 벡터 재정렬을 거쳐 관련 특허를 최대 5건 선택합니다.
- `PATENT_SOURCE=chroma`는 오프라인으로 구축한 영구 ChromaDB 코퍼스를 먼저 검색합니다. 관련도 기준을 통과한 결과가 `HYBRID_MIN_PATENTS`(기본 5건) 이상이면 기존 색인만으로 분석하고 KIPRIS를 호출하지 않습니다. 결과가 부족하면 KIPRIS를 보충 호출해 후보를 재정렬하고, 관련 특허를 기존 컬렉션에 증분 저장한 뒤 기존 결과와 합쳐 최대 5건을 분석합니다. KIPRIS가 실패하면 저장된 검색 결과로 계속 진행하며 화면에 한계를 표시합니다.
- `indexing.py`는 검색어로 특허를 수집하고 상세 서지/청구항 조회를 시도한 다음, 확보된 초록·상세 설명·청구항을 청킹해 persistent ChromaDB에 넣습니다. 상세 데이터가 없는 레코드는 초록만 색인하며, 작업 결과에 실제 청구항 보유 건수를 표시합니다.
- 색인 범위는 실행한 검색어로 수집한 코퍼스이며 KIPRIS 전체를 뜻하지 않습니다. 관심 분야별 검색어를 넓혀 색인하고 새 데이터가 생기면 같은 작업을 다시 실행합니다.
- 시장 검색은 OpenAI Responses API 웹 검색 도구를 사용합니다. `MARKET_SEARCH_MODEL`에는 웹 검색이 가능한 모델을 설정하세요. 출처 링크가 연결된 검색 요약을 화면에 표시합니다. 이 요약은 원문을 직접 수집·검증한 결과가 아닙니다.
- 아이디어 전문은 KIPRIS에 보내지 않습니다. LLM 파싱과 시장 검색에는 아이디어 내용 또는 검색 질문이 외부 모델 제공자에게 전달됩니다.

## 특허 데이터화와 평가

선정된 특허는 해결 과제·기술 구성·적용 분야·문서에 기재된 효과로 정리하고, 각 필드에 원문 구절과 위치(초록/청구항)를 붙입니다. 서버가 그 구절이 원문에 실제 들어 있는지 확인합니다. 데이터화 결과는 JSON으로 내려받을 수 있습니다.

사업성 항목은 0~5점입니다. `I`는 사용자 입력, `P1`은 첫 번째 특허, `M1`은 첫 번째 시장 자료를 뜻합니다. 점수는 모델 제안을 그대로 사용하지 않고 백엔드에서 근거 성숙도에 따라 제한합니다. 고객 검증이 없는 문제 설명은 최대 3점, 실제 고객 수요 검증이 없는 공개 웹 자료는 최대 2~3점, 청구항 기반 비교가 없으면 기술 차별성 최대 1점, 시제품·성능·원가 검증이 없으면 구현 가능성 최대 2점, 아이디어별 지불 의사 확인이 없으면 수익화 가능성 최대 2점입니다. 따라서 현재 자료만으로는 100점이 나올 수 없으며 높은 점수는 인터뷰·시제품·구매의사 같은 검증 데이터가 추가된 뒤에만 가능해야 합니다.

| 항목 | 평가에 필요한 출처 |
|---|---|
| 문제의 명확성 | 입력의 문제 설명 (`I`) |
| 시장 수요 | URL이 연결된 시장 자료 (`M`) |
| 기술 차별성 | 특허 데이터와 원문 인용 (`P`) |
| 구현 가능성 | 특허의 구성·기재 내용 (`P`) |
| 수익화 가능성 | 가격·지불자 관련 시장 근거 (`M`) |

근거가 부족하거나 검색에 실패하면 0점 대신 평가 보류합니다. 환산은 항상 전체 25점 만점을 분모로 사용하므로, 미평가 항목이 많을수록 잠정 점수가 높아지지 않습니다. 별도 특허 검토 영역에는 벡터 검색 유사도, 청구항 원문 확보 여부, 상세 대조 우선순위를 표시합니다. 이는 선행기술 검색의 검토 신호이며 신규성·진보성·침해·등록 가능성 판정이 아닙니다. 출원 전에는 청구항 단위 조사와 전문가 검토가 필요합니다. 특허 초록의 효과를 실제 검증으로 간주하지 않으며, 시장 자료를 매출·성공 확률로 해석하지 않습니다.

## KIPRIS API

기본 `access_key` 프로필은 KIPRIS Plus 특허·실용 공개/등록공보 `freeSearchInfo`를 사용하고, 오프라인 색인 시에는 특허별 `getBibliographyDetailInfoSearch` 상세 조회도 시도합니다. `service_key` 프로필은 `getWordSearch` 형식을 사용합니다. 색인 작업은 검색 후보와 상세 조회 결과에 있는 초록·청구항·상세 설명만 저장합니다. 실제 키로 KIPRIS를 호출하지 않은 모의 테스트까지만 완료했으며, API 필드 차이가 있으면 `backend/kipris.py`의 필드 별칭을 보완해야 합니다.

공식 자료: [특허 API freeSearchInfo](https://plus.kipris.or.kr/portal/popup/DBII_000000000000001/SC002/ADI_0000000000010162/apiDescriptionSearch.do) · [특허 API getWordSearch](https://plus.kipris.or.kr/eng/data/clasList/DBII_000000000000001/view.do?clasKeyword=&entYn=&kppSCode=KPP010101&menuNo=&subTab=SC001) · [KIPRIS Plus 개발 가이드](https://plus.kipris.or.kr/portal/bbs/view.do?bbsId=B0000001&delCode=0&menuNo=200015&nttId=1060&pageIndex=1).

## 팀 구조와 API

| 역할 | 코드 |
|---|---|
| UI | `frontend/app.py` |
| API·통합·점수 | `backend/main.py`, `models.py`, `scoring.py` |
| KIPRIS·임베딩 검색 | `backend/kipris.py`, `retrieval.py` |
| LLM·특허 데이터화·시장 검색 | `backend/analysis.py`, `market.py` |

`POST /api/parse-idea` 요청은 `{"idea":"아이디어 자유 입력 (10~6000자)"}`이며 자동 작성된 다섯 항목을 반환합니다. `POST /api/analyze`에는 원문 아이디어와 사용자가 확인한 다섯 항목을 보냅니다. 응답의 `business_plan_draft`는 검증된 분석 결과를 묶은 편집 가능한 Markdown 초안이며, 추가 LLM 호출 없이 생성합니다. 기존 `{"idea":"..."}` 단독 요청도 지원합니다. `GET /api/health`는 모드, 검색 범위, KIPRIS 키 설정 여부를 반환합니다. 프로젝트 구성과 세부 데이터 필드는 [팀 계획](docs/TEAM_PLAN.md)과 [데이터 출처 설명](docs/DATA_SOURCES.md)을 참고하세요.

## 검증

```bash
IDEALENS_MODE=demo python -m pytest -q
```

샘플 아이디어 5건, KIPRIS XML 두 응답 형식 및 오류 처리, 실제 서비스 순서를 모의하는 전체 API 흐름, 시장 인용 URL 처리, 출처 부족 시 점수 보류, Streamlit 입력→결과를 검증합니다. 자동 테스트는 실제 API 계정 권한과 검색의 의미 정확성을 보증하지 않습니다.

## 보안과 한계

키는 `.env`에만 둡니다. 서버는 아이디어나 분석 결과를 DB·일반 로그에 저장하지 않지만, 화면 세션과 내려받은 보고서에는 내용이 남을 수 있습니다. 외부 API로 아이디어 일부와 검색된 특허 문서가 전송됩니다. 현재 로그인·사용량 제한이 없어 공개 배포 전에 접근 제어를 추가해야 합니다. 법률 의견, 시장 규모·매출 전망, 사업 성공 확률은 제공하지 않습니다.

### 구현 확인 기록

2026-10-20, Python 3.12에서 `37 passed`를 확인했습니다. 오프라인 ChromaDB 저장/재사용, 증분 갱신, 청구항 번호 보존 테스트를 포함합니다. 실제 KIPRIS/OpenAI 요청은 실행하지 않았으므로 구매한 계정의 응답 필드·한도는 실제 색인 실행 결과로 확인해야 합니다.
