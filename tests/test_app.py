import json
import time
from pathlib import Path
from types import SimpleNamespace as NS
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.models import *
from backend.main import app, search_patents
from backend.analysis import validate_patent_data
from backend.analysis import structured
from backend.analysis import parse_idea
from backend.scoring import enforce_and_score
from backend.kipris import KiprisClient, KiprisError, parse_response
from backend.market import parse_market_response, search_market
from backend.retrieval import demo_embedding, load_patents, chunks, persist_patent_index, PersistentPatentRetriever
ROOT=Path(__file__).resolve().parents[1]
SAMPLES=json.loads((ROOT/'data/sample_ideas.json').read_text())

def parsed():
    return ParsedIdea(title='자동 급수',problem=InputFact(value='물주기 어려움',quote='물주기'),
        technology=InputFact(value='토양 센서',quote='토양 센서'),customers=None,difference=None,revenue=None,
        keywords=['토양','센서','급수'],patent_queries=['토양 센서'],market_query='자동 급수 한국 수요 가격',
        assumptions=[],missing_information=[])

def patent_rows(n=1):
    return [PatentData(citation_id=i,problem=None,technology=PatentFact(value='센서 급수',quote='센서',section='abstract'),
        application=None,effect=None,limitations=[]) for i in range(1,n+1)]

def market():
    return MarketResult(status='completed',warning='',evidence=[MarketEvidence(id='M1',summary='시장 수요 사례',
        sources=[MarketSource(title='테스트 자료',url='https://example.org/report')],retrieved_at='2026-10-19')])

def output(scores,n=1):
    return Generated(summary='요약',comparisons=[Comparison(citation_id=i,similarity='센서 사용',difference='추가 비교 필요',uncertainty='초록만 확보') for i in range(1,n+1)],
        evaluations=[Evaluation(criterion=c,score=s,reason='평가 이유',evidence='근거 구절',source_ids=refs)
        for c,s,refs in zip(CRITERIA,scores,[['I'],['M1'],['P1'],['P1'],['M1']])],improvements=['a','b','c'])

def test_scores():
    assert enforce_and_score(parsed(),output([3,4,3,5,5]),patent_rows(),market())==(40.0,5,'종합 점수')
    assert enforce_and_score(parsed(),output([3,None,3,None,None]),patent_rows(),market())==(16.0,2,'잠정 점수')
    assert enforce_and_score(parsed(),output([None]*5),patent_rows(),market())==(None,0,'평가 보류')
    assert enforce_and_score(parsed(),output([5]*5),patent_rows(),market(),demo=True)==(None,0,'평가 보류')

def test_plausible_idea_alone_cannot_reach_a_high_score():
    result=output([5]*5)
    score,count,_=enforce_and_score(parsed(),result,patent_rows(),market())
    assert score==40 and count==5
    assert [row.score for row in result.evaluations]==[3,2,1,2,2]

def test_technical_sources_cannot_support_market_scores():
    result=output([3]*5)
    for row in result.evaluations: row.source_ids=['P1']
    assert enforce_and_score(parsed(),result,patent_rows(),MarketResult(status='failed',warning='',evidence=[]))==(12.0,2,'잠정 점수')
    assert [r.score for r in result.evaluations]==[None,None,1,2,None]

def test_unknown_citations_are_withheld_without_losing_report():
    result=output([3]*5); result.evaluations[0].source_ids=['M99']
    enforce_and_score(parsed(),result,patent_rows(),market())
    assert result.evaluations[0].score is None
    assert result.evaluations[0].source_ids == []
    result=output([3]*5); result.comparisons*=2
    enforce_and_score(parsed(),result,patent_rows(),market())
    assert len(result.comparisons)==1
    assert result.comparisons[0].similarity=='센서 사용'

def test_duplicate_criteria():
    result=output([3]*5).model_dump(); result['evaluations'][1]=result['evaluations'][0]
    with pytest.raises(ValueError): Generated.model_validate(result)

def test_real_mode_rejects_demo():
    with pytest.raises(ValueError): load_patents(ROOT/'data/demo_patents.json',False)

def test_patent_chunks_keep_abstract_separate_and_claim_parent_context():
    patent=Patent(id='p1',title='발명의 명칭',publication_number='pub1',
        abstract='초록 내용입니다. 발명의 개요를 설명합니다.',claims='청구항 1\n배터리 냉각 장치를 포함한다.\n청구항 2\n제1항에 있어서, 냉각 유로를 포함한다.',
        url='https://example.org/patent',source='test')
    rows=chunks([patent])
    assert rows[0].section=='초록' and rows[0].quote==patent.abstract
    parent,child=rows[1:]
    assert parent.claim_number==1 and parent.parent_claim_numbers==()
    assert child.claim_number==2 and child.parent_claim_numbers==(1,)
    assert '배터리 냉각 장치를 포함한다.' in child.document
    assert child.quote=='제1항에 있어서, 냉각 유로를 포함한다.'

def test_detailed_description_uses_recursive_chunks_with_overlap():
    from backend.retrieval import _recursive_chunks
    text=('첫 번째 설명 문장입니다. '*80)+'\n\n'+('두 번째 상세 설명 문장입니다. '*80)
    result=_recursive_chunks(text,size=200,overlap=30)
    assert len(result)>2 and all(len(part)<=200 for part in result)
    assert any(set(result[i][-25:]) & set(result[i+1][:25]) for i in range(len(result)-1))

def fake_embedding_client():
    def create(model,input):
        return NS(data=[NS(index=i,embedding=demo_embedding(text)) for i,text in enumerate(input)])
    return NS(embeddings=NS(create=create))

def test_persistent_index_is_reused_and_incrementally_updated(tmp_path):
    patent=Patent(id='p1',title='배터리 냉각',publication_number='pub1',
        abstract='배터리 냉각 장치의 발명을 설명하는 초록입니다.',
        claims='청구항 1\n배터리 냉각 장치와 유로를 포함한다.',
        url='https://example.org/patent',source='KIPRIS Plus')
    fake=fake_embedding_client()
    first=persist_patent_index([patent],fake,'test-model',tmp_path,'test_collection',['배터리 냉각'])
    retriever=PersistentPatentRetriever(tmp_path,'test_collection',fake,'test-model',0)
    evidence=retriever.search('배터리 냉각 유로')
    assert first['patents']==1 and first['claims']==1
    assert evidence and evidence[0].patent.id=='p1'
    assert evidence[0].claim_number==1
    updated=patent.model_copy(update={'claims':''})
    second=persist_patent_index([updated],fake,'test-model',tmp_path,'test_collection',['배터리 열관리'])
    assert second['patents']==1 and second['claims']==1
    assert second['chunks']==first['chunks']

def test_health_exposes_persistent_corpus_scope(tmp_path,monkeypatch):
    patent=Patent(id='p1',title='배터리 냉각',publication_number='pub1',
        abstract='배터리 냉각 장치의 발명을 설명하는 초록입니다.',claims='청구항 1\n배터리 냉각 장치를 포함한다.',
        url='https://example.org/patent',source='KIPRIS Plus')
    persist_patent_index([patent],fake_embedding_client(),'test-model',tmp_path,'test_collection',['배터리 냉각'])
    for key,value in {'IDEALENS_MODE':'rag','PATENT_SOURCE':'chroma','OPENAI_API_KEY':'test-key',
        'PATENT_INDEX_PATH':str(tmp_path),'PATENT_INDEX_COLLECTION':'test_collection','EMBEDDING_MODEL':'test-model'}.items():
        monkeypatch.setenv(key,value)
    with TestClient(app) as client:
        health=client.get('/api/health').json()
    assert health['patent_source']=='chroma'
    assert '오프라인 KIPRIS 코퍼스 1건' in health['scope']
    assert '청구항 포함 1건' in health['scope']

def evidence_for(identifier,similarity=.8):
    patent=Patent(id=str(identifier),title=f'특허 {identifier}',publication_number=f'pub{identifier}',
        abstract=f'배터리 냉각 장치에 관한 특허 초록 {identifier}입니다.',url=f'https://example.org/{identifier}',source='KIPRIS Plus')
    return Evidence(citation_id=identifier,patent=patent,section='초록',quote=patent.abstract,similarity=similarity)

def test_hybrid_search_skips_kipris_when_index_has_enough_matches(monkeypatch):
    with TestClient(app):
        monkeypatch.setattr(app.state,'source','chroma')
        monkeypatch.setenv('HYBRID_MIN_PATENTS','5')
        monkeypatch.setattr(app.state,'retriever',NS(search=lambda q:[evidence_for(i) for i in range(1,6)],close=lambda:None))
        monkeypatch.setattr(app.state,'kipris',NS(search=lambda q:pytest.fail('KIPRIS should not be called')))
        matches,strategy,notices=search_patents('배터리 냉각',['배터리 냉각'])
    assert len(matches)==5 and strategy=='chroma_only' and notices==[]
    assert all(row.retrieval_source=='기존 Chroma 색인' for row in matches)

def test_hybrid_search_supplements_persists_and_merges(monkeypatch,tmp_path):
    persisted=[]; refreshed=[]
    existing=[evidence_for(1,.75),evidence_for(2,.7)]
    fresh=[evidence_for(3,.91),evidence_for(4,.82),evidence_for(1,.8)]
    candidates=[row.patent for row in fresh]
    index=NS(search=lambda q:existing,collection=object(),patent_ids={'1','2'},refresh=lambda:refreshed.append(True),close=lambda:None)
    fake_searcher=NS(search=lambda q:fresh,close=lambda:None)
    with TestClient(app):
        monkeypatch.setattr(app.state,'source','chroma')
        monkeypatch.setattr(app.state,'retriever',index)
        monkeypatch.setattr(app.state,'index_path',tmp_path)
        monkeypatch.setattr(app.state,'index_collection','test')
        monkeypatch.setattr(app.state,'client',NS(close=lambda:None))
        monkeypatch.setenv('HYBRID_MIN_PATENTS','5')
        monkeypatch.setattr(app.state,'kipris',NS(search=lambda queries:candidates,enrich=lambda p:p))
        monkeypatch.setattr('backend.main.make_retriever',lambda rows:fake_searcher)
        def persist(rows,client,model,path,name,queries,collection=None):
            persisted.extend(rows); assert collection is index.collection
        monkeypatch.setattr('backend.main.persist_patent_index',persist)
        matches,strategy,notices=search_patents('배터리 냉각',['배터리 냉각'])
    assert strategy=='chroma_plus_kipris' and notices==[]
    assert [row.patent.id for row in matches]==['3','4','1','2']
    assert [row.citation_id for row in matches]==[1,2,3,4]
    assert [row.id for row in persisted]==['3','4','1']
    assert refreshed

def test_hybrid_search_keeps_index_results_when_kipris_fails(monkeypatch):
    existing=[evidence_for(1,.75)]
    with TestClient(app):
        monkeypatch.setattr(app.state,'source','chroma')
        monkeypatch.setattr(app.state,'retriever',NS(search=lambda q:existing,close=lambda:None))
        def fail(queries): raise RuntimeError('provider detail must not leak')
        monkeypatch.setattr(app.state,'kipris',NS(search=fail))
        matches,strategy,notices=search_patents('배터리 냉각',['배터리 냉각'])
    assert [row.patent.id for row in matches]==['1'] and strategy=='chroma_kipris_failed'
    assert notices and 'provider detail' not in notices[0]

def evidence():
    p=Patent(id='1',title='센서',publication_number='1',abstract='토양 센서로 물을 공급합니다.',url=None,source='test')
    return [Evidence(citation_id=1,patent=p,section='초록',quote=p.abstract,similarity=.9)]

def test_patent_quotes_and_coverage():
    dataset=PatentDataset(patents=patent_rows())
    assert validate_patent_data(dataset,evidence())
    dataset.patents[0].technology.quote='원문에 없는 효과'
    cleaned=validate_patent_data(dataset,evidence())
    assert cleaned[0].technology is None
    assert validate_patent_data(PatentDataset(patents=[]),evidence())[0].technology is None

def test_invalid_patent_quote_withholds_only_that_field():
    dataset=PatentDataset(patents=patent_rows())
    dataset.patents[0].technology.quote='원문에 없는 효과'
    cleaned=validate_patent_data(dataset,evidence())
    assert cleaned[0].technology is None
    assert any('보류' in x for x in cleaned[0].limitations)

def test_missing_patent_row_is_returned_as_unverified():
    cleaned=validate_patent_data(PatentDataset(patents=[]),evidence())
    assert len(cleaned)==1
    assert cleaned[0].technology is None

def test_input_quote_uses_normalized_whitespace_and_defers_bad_fact():
    from backend.analysis import _quote_is_grounded
    assert _quote_is_grounded('센서로\n 물을 공급','센서로 물을')
    assert not _quote_is_grounded('센서로 물을 공급','전지를 교체합니다')

def xml(n=1,modern=True):
    if modern:
        rows=''.join(f'<PatentUtilityInfo><ApplicationNumber>{i}</ApplicationNumber><InventionName>토양 수분 센서 {i}</InventionName><Abstract>토양 수분 센서로 물을 자동 급수 펌프로 공급합니다.</Abstract><OpeningNumber>100{i}</OpeningNumber><Applicant>테스트</Applicant></PatentUtilityInfo>' for i in range(1,n+1))
        return f'<response><TotalSearchCount>{n}</TotalSearchCount>{rows}</response>'.encode()
    return b'<response><header><resultCode>00</resultCode></header><body><items><item><applicationNumber>1</applicationNumber><inventionTitle>Sensor</inventionTitle><astrtCont>Soil sensor waters plants automatically.</astrtCont><openNumber>2</openNumber></item></items></body></response>'

@pytest.mark.parametrize('profile',['access_key','service_key'])
def test_kipris_adapter(profile):
    def handler(request):
        assert request.url.scheme=='https'
        assert request.url.params['accessKey' if profile=='access_key' else 'ServiceKey']=='test'
        assert request.url.params['word']=='토양 센서'
        return httpx.Response(200,content=xml(modern=profile=='access_key'))
    rows=KiprisClient('test',profile,httpx.MockTransport(handler)).search(['토양 센서','토양 센서'])
    assert len(rows)==1 and rows[0].application_number=='1' and rows[0].claims==''
    assert str(rows[0].url).endswith('queryText=1')

@pytest.mark.parametrize('body',[b'<html>error</html>',b'<response><resultCode>30</resultCode></response>',b'not xml',b'<!DOCTYPE x><response/>'])
def test_kipris_error_not_empty_results(body):
    with pytest.raises(KiprisError): parse_response(body)

def test_kipris_empty_results():
    assert parse_response(xml(0))==[]

def test_kipris_enrich_reads_claims_for_offline_indexing():
    def handler(request):
        assert request.url.path.endswith('getBibliographyDetailInfoSearch')
        assert request.url.params['applicationNumber']=='1'
        payload='<response><claims><claim><claimNumber>1</claimNumber><claimText>배터리 냉각 장치</claimText></claim><claim><claimNumber>2</claimNumber><claimText>제1항에 있어서 유로를 포함</claimText></claim></claims></response>'
        return httpx.Response(200,content=payload.encode())
    patent=Patent(id='1',title='배터리 냉각',publication_number='pub1',abstract='배터리 냉각 장치를 설명하는 초록입니다.',url='https://example.org/patent',source='KIPRIS Plus',application_number='1')
    enriched=KiprisClient('test',transport=httpx.MockTransport(handler)).enrich(patent)
    assert '청구항 1' in enriched.claims and '청구항 2' in enriched.claims and '배터리 냉각' in enriched.claims

def web_response(cited=True,searched=True):
    text='한국 자동 급수 제품의 가격 사례입니다. 기준일 미확인. [출처]'
    content={'type':'output_text','text':text,'annotations':[{'type':'url_citation','start_index':text.index('[출처]'),'end_index':len(text),'title':'공식 제품','url':'https://example.org/product'}] if cited else []}
    items=([{'type':'web_search_call','status':'completed'}] if searched else [])+[{'type':'message','content':[content]}]
    return NS(model_dump=lambda:{'output':items})

def test_market_citations_required():
    assert len(parse_market_response(web_response()).evidence)==1
    assert parse_market_response(web_response(cited=False)).status=='no_results'
    assert parse_market_response(web_response(searched=False)).status=='no_results'

def test_market_failure_is_explicit():
    def fail(**kwargs): raise RuntimeError('secret')
    result=search_market(parsed(),False,NS(responses=NS(create=fail)),'model')
    assert result.status=='failed' and not result.evidence and 'secret' not in result.warning

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('IDEALENS_MODE','demo')
    with TestClient(app) as client: yield client

@pytest.mark.parametrize('idea',SAMPLES)
def test_five_demo_ideas(client,idea):
    response=client.post('/api/analyze',json=idea)
    assert response.status_code==200,response.text
    report=response.json()
    assert report['mode']=='demo' and report['score'] is None
    assert 0<len(report['evidence'])<=5
    assert len(report['patent_data'])==len(report['evidence'])
    assert report['market']['status']=='demo' and 'parsed_idea' in report
    assert '# ' in report['business_plan_draft']
    assert '## 4. 선행특허 검토' in report['business_plan_draft']
    assert '## 5. 시장·경쟁 근거' in report['business_plan_draft']
    assert '## 8. 우선순위별 다음 검증 과제' in report['business_plan_draft']

def test_single_input_validation(client):
    for payload in [{},{'idea':'  '},{'title':'old input'},{'idea':'x'*6001}]:
        assert client.post('/api/analyze',json=payload).status_code==422
    assert client.get('/api/health').status_code==200

def test_no_results(client,monkeypatch):
    monkeypatch.setattr(app.state.retriever,'search',lambda q:[])
    result=client.post('/api/analyze',json=SAMPLES[0]).json()
    assert result['status']=='no_results' and result['patent_data']==[] and result['generated']['comparisons']==[]
    assert '이는 관련 특허가 없다는 결론이 아닙니다.' in result['business_plan_draft']
    assert '시장 자료 공백' in result['business_plan_draft']

def test_provider_failure(client,monkeypatch):
    def fail(q): raise RuntimeError('secret')
    monkeypatch.setattr(app.state.retriever,'search',fail)
    response=client.post('/api/analyze',json=SAMPLES[0])
    assert response.status_code==502 and 'secret' not in response.text

def test_structured_failure_reports_stage_without_prompt_content(client,monkeypatch):
    fake=NS(chat=NS(completions=NS(parse=lambda **kw:NS(choices=[NS(message=NS(parsed=None))]))),close=lambda:None)
    monkeypatch.setattr(app.state,'mode','rag')
    monkeypatch.setattr(app.state,'client',fake)
    response=client.post('/api/analyze',json={'idea':'배터리 충전 속도를 높이는 기술 아이디어입니다.'})
    assert response.status_code==502
    assert '아이디어 파싱 단계' in response.json()['detail']
    assert '배터리' not in response.text

def test_structured_ai_failure_logs_only_safe_metadata(caplog):
    class ProviderError(RuntimeError):
        status_code=400
        code='invalid_json_schema'
    def fail(**kwargs): raise ProviderError('sensitive request payload')
    fake=NS(chat=NS(completions=NS(parse=fail)))
    with pytest.raises(Exception):
        structured(fake,'model','prompt',{'idea':'private idea'},ParsedIdea,'사업성 평가')
    assert 'stage=사업성 평가' in caplog.text
    assert 'error_type=ProviderError' in caplog.text
    assert 'status=400' in caplog.text and 'invalid_json_schema' in caplog.text
    assert 'sensitive request payload' not in caplog.text
    assert 'private idea' not in caplog.text

def test_unquoted_auto_filled_facts_are_kept_as_hypotheses(monkeypatch):
    idea=Idea(idea='전기차 배터리 급속충전 기술로 충전 시간을 줄이고 싶습니다.')
    draft=ParsedIdea(title='급속 충전',
        problem=InputFact(value='충전 대기 시간을 줄인다',quote=idea.idea),
        technology=InputFact(value='고출력 충전 제어',quote='원문에 없는 인용'),
        customers=InputFact(value='전기차 운전자와 충전소 운영사',quote='전기차 운전자',basis='AI 가설'),
        difference=InputFact(value='기존 대비 충전 시간을 단축',quote='원문에 없는 인용'),
        revenue=InputFact(value='충전기 판매와 사업자 라이선스',quote='원문에 없는 인용'),
        keywords=['전기차','급속충전'],patent_queries=['전기차 급속충전'],market_query='전기차 급속충전 시장',
        assumptions=[],missing_information=[])
    monkeypatch.setattr('backend.analysis.structured',lambda *args:draft)
    result=parse_idea(idea,False,object(),'test-model')
    assert all(getattr(result,key) is not None for key in ('problem','technology','customers','difference','revenue'))
    assert result.technology.basis=='AI 가설' and result.technology.quote==''
    assert result.customers.basis=='AI 가설' and result.customers.quote==''
    assert any('revenue:' in item for item in result.assumptions)

def test_timeout(client,monkeypatch):
    monkeypatch.setenv('ANALYSIS_TIMEOUT','0.01')
    def slow(q): time.sleep(.1); return []
    monkeypatch.setattr(app.state.retriever,'search',slow)
    assert client.post('/api/analyze',json=SAMPLES[0]).status_code==504
    assert client.post('/api/analyze',json=SAMPLES[0]).status_code==504
    time.sleep(.15)

def test_duplicate_analysis_requests_share_inflight_work(client,monkeypatch):
    calls=[]
    original=app.state.retriever.search
    def slow(q):
        calls.append(q); time.sleep(.15); return original(q)
    monkeypatch.setattr(app.state.retriever,'search',slow)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(client.post,'/api/analyze',json=SAMPLES[0])
        time.sleep(.02)
        second=pool.submit(client.post,'/api/analyze',json=SAMPLES[0])
        responses=[first.result(),second.result()]
    assert [r.status_code for r in responses]==[200,200]
    assert responses[0].json()==responses[1].json()
    assert len(calls)==1

def test_live_pipeline_with_mocked_external_services(client,monkeypatch):
    events=[]
    def http_handler(request):
        events.append('kipris'); return httpx.Response(200,content=xml(7))
    def completion(**kwargs):
        schema=kwargs['response_format']; payload=json.loads(kwargs['messages'][1]['content'])
        if schema is ParsedIdea: events.append('parse'); result=parsed()
        elif schema is PatentDataset: events.append('extract'); result=PatentDataset(patents=patent_rows(len(payload['patents'])))
        else: events.append('score'); result=output([3]*5,len(payload['patent_data']))
        return NS(choices=[NS(message=NS(parsed=result))])
    def embed(**kwargs):
        events.append('embed'); return NS(data=[NS(index=i,embedding=demo_embedding(t)) for i,t in enumerate(kwargs['input'])])
    def web(**kwargs): events.append('market'); return web_response()
    fake=NS(chat=NS(completions=NS(parse=completion)),embeddings=NS(create=embed),responses=NS(create=web),close=lambda:None)
    monkeypatch.setattr(app.state,'mode','rag'); monkeypatch.setattr(app.state,'source','kipris')
    monkeypatch.setattr(app.state,'client',fake)
    monkeypatch.setattr(app.state,'kipris',KiprisClient('test',transport=httpx.MockTransport(http_handler)))
    response=client.post('/api/analyze',json={'idea':'물주기 문제를 해결하는 토양 센서 자동 급수입니다.'})
    assert response.status_code==200,response.text
    report=response.json()
    assert len(report['evidence'])==len(report['patent_data'])==5
    assert report['score']==40 and report['evaluated_count']==5
    assert report['patent_review']['status'] in {'유사 문헌 상세 검토 권장','검색 결과 비교'}
    assert len(report['patent_review']['items'])==len(report['evidence'])
    assert events[0]=='parse' and events.index('kipris')<events.index('extract')<events.index('market')<events.index('score')
    assert len({e['patent']['id'] for e in report['evidence']})==5
