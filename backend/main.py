import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from .models import Idea, AnalysisRequest, ParsedIdea, InputFact, Report, PatentReview, PatentReviewItem
from .retrieval import Retriever, PersistentPatentRetriever, load_patents, persist_patent_index, tokens
from .analysis import parse_idea, extract_patents, generate, AnalysisStageError
from .scoring import enforce_and_score
from .kipris import KiprisClient, KiprisError
from .market import search_market
from .business_plan import build_business_plan

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')

@asynccontextmanager
async def lifespan(app):
    mode=os.getenv('IDEALENS_MODE','demo')
    if mode not in {'demo','rag'}:
        raise RuntimeError('IDEALENS_MODE must be demo or rag')
    source='demo' if mode=='demo' else os.getenv('PATENT_SOURCE','kipris')
    if source not in {'demo','kipris','local','chroma'}:
        raise RuntimeError('PATENT_SOURCE must be demo, kipris, local, or chroma')
    app.state.mode,app.state.source=mode,source
    app.state.client=None
    if mode=='rag':
        from openai import OpenAI
        app.state.client=OpenAI(timeout=40,max_retries=0)
    app.state.retriever=None
    if source in {'demo','local'}:
        path=ROOT/('data/demo_patents.json' if source=='demo' else os.getenv('PATENT_DATA_PATH','data/patents.json'))
        patents=load_patents(path,mode=='demo')
        app.state.retriever=make_retriever(patents)
        app.state.scope=f'{"가상 데모" if mode=="demo" else "사전 수집"} {len(patents)}건 내 최대 5건 검색'
    elif source=='chroma':
        index_path=ROOT/os.getenv('PATENT_INDEX_PATH','data/chroma')
        collection_name=os.getenv('PATENT_INDEX_COLLECTION','idealens_patents_v1')
        app.state.index_path=index_path
        app.state.index_collection=collection_name
        app.state.retriever=PersistentPatentRetriever(index_path,collection_name,
            app.state.client,os.getenv('EMBEDDING_MODEL','text-embedding-3-small'),float(os.getenv('MIN_RELEVANCE','0.3')))
        index_metadata=app.state.retriever.metadata
        try: queries=', '.join(json.loads(index_metadata.get('queries','[]')))
        except (TypeError,ValueError): queries='저장 인덱스'
        patents={row.patent.id:row.patent for row in app.state.retriever.rows}
        claims=sum(bool(p.claims) for p in patents.values())
        app.state.scope=f"오프라인 KIPRIS 코퍼스 {len(patents)}건 · 청구항 포함 {claims}건 · 검색어 {queries or '미기록'} · {os.getenv('HYBRID_MIN_PATENTS','5')}건 미만이면 KIPRIS 보충 검색·색인 · 갱신 {index_metadata.get('indexed_at','정보 없음')}"
    else:
        app.state.scope='KIPRIS 실시간 검색 · 검색어 최대 3개 × 후보 최대 30건 → 벡터 재정렬 → 관련 특허 최대 5건'
    app.state.kipris=KiprisClient(os.getenv('KIPRIS_API_KEY',''),os.getenv('KIPRIS_API_PROFILE','access_key'))
    app.state.analysis_guard=asyncio.Lock()
    app.state.analysis_task=None
    app.state.analysis_fingerprint=None
    yield
    if app.state.retriever:
        app.state.retriever.close()
    if app.state.client:
        app.state.client.close()

def make_retriever(patents):
    return Retriever(patents,app.state.mode=='demo',app.state.client,
        os.getenv('EMBEDDING_MODEL','text-embedding-3-small'),float(os.getenv('MIN_RELEVANCE','0.3')),
        {'demo':'데모 코퍼스','local':'로컬 사전 색인','kipris':'KIPRIS 보충 검색'}.get(app.state.source,'특허 색인'))

app=FastAPI(title='IdeaLens',version='0.2.0',lifespan=lifespan)

@app.get('/api/health')
def health():
    return {'status':'ok','mode':app.state.mode,'scope':app.state.scope,
        'patent_source':app.state.source,
        'kipris_key_configured':bool(os.getenv('KIPRIS_API_KEY')),
        'market_search_enabled':os.getenv('MARKET_SEARCH_ENABLED','true').lower()=='true'}

@app.post('/api/parse-idea',response_model=ParsedIdea)
def parse_idea_endpoint(idea: Idea):
    try:
        return parse_idea(idea,app.state.mode=='demo',app.state.client,os.getenv('LLM_MODEL','gpt-4o-mini'))
    except AnalysisStageError as exc:
        raise HTTPException(502,f'{exc.stage} 단계의 AI 응답을 읽지 못했습니다. 잠시 후 다시 시도해주세요.')

def analyze_sync(idea):
    demo=app.state.mode=='demo'
    client=app.state.client
    model=os.getenv('LLM_MODEL','gpt-4o-mini')
    parsed=parse_idea(idea,demo,client,model)
    return analyze_parsed(idea,parsed,demo,client,model)

def search_patents(query, patent_queries):
    """Use the persistent corpus first; call KIPRIS only when relevant coverage is short."""
    if app.state.source!='chroma':
        if app.state.source=='kipris':
            candidates=app.state.kipris.search(patent_queries)
            if not candidates:return [],'kipris_no_candidates',[]
            searcher=make_retriever(candidates)
            try:return searcher.search(query),'kipris_live',[]
            finally:searcher.close()
        return app.state.retriever.search(query),app.state.source,[]

    index=app.state.retriever
    existing=[item.model_copy(update={'retrieval_source':'기존 Chroma 색인'}) for item in index.search(query)]
    required=max(1,int(os.getenv('HYBRID_MIN_PATENTS','5')))
    if len(existing)>=required:
        return existing,'chroma_only',[]

    notices=[]
    try:
        candidates=app.state.kipris.search(patent_queries)
        candidates=[app.state.kipris.enrich(patent) for patent in candidates]
    except Exception:
        return existing,'chroma_kipris_failed',['기존 색인 결과가 부족해 KIPRIS 보충 검색을 시도했지만 실패했습니다. 기존 검색 근거만으로 분석을 이어갑니다.']
    if not candidates:
        return existing,'chroma_kipris_empty',['기존 색인 결과가 부족했으며 KIPRIS에서도 새 후보를 찾지 못했습니다.']

    try:
        candidate_searcher=make_retriever(candidates)
        fresh=candidate_searcher.search(query)
    except Exception:
        return existing,'chroma_embedding_failed',['KIPRIS 후보를 가져왔지만 벡터 검색에 실패해 기존 색인 근거만 사용했습니다.']
    finally:
        if 'candidate_searcher' in locals():candidate_searcher.close()
    fresh=[item.model_copy(update={'retrieval_source':'KIPRIS 보충 검색'}) for item in fresh]

    saved=False
    if fresh:
        selected={item.patent.id:item.patent for item in fresh}
        try:
            persist_patent_index(list(selected.values()),app.state.client,os.getenv('EMBEDDING_MODEL','text-embedding-3-small'),
                app.state.index_path,app.state.index_collection,patent_queries,collection=index.collection)
            index.refresh()
            saved=True
        except Exception:
            notices.append('이번 분석에서 찾은 KIPRIS 특허는 사용했지만 영구 색인 저장에 실패했습니다. 다음 분석에서 다시 검색될 수 있습니다.')
    else:
        notices.append('KIPRIS 후보 중 관련도 기준을 통과한 특허가 없어 기존 색인 결과를 유지했습니다.')

    best={}
    for item in [*existing,*fresh]:
        current=best.get(item.patent.id)
        if current is None or item.similarity>current.similarity:best[item.patent.id]=item
    combined=sorted(best.values(),key=lambda item:item.similarity,reverse=True)[:5]
    combined=[item.model_copy(update={'citation_id':i}) for i,item in enumerate(combined,1)]
    strategy=('chroma_plus_kipris' if saved else 'chroma_plus_kipris_unpersisted') if fresh else 'chroma_kipris_no_match'
    return combined,strategy,notices

def analyze_parsed(idea, parsed, demo, client, model):
    fact_values={k:(getattr(parsed,k).value if getattr(parsed,k) else '') for k in ('problem','technology','customers','difference','revenue')}
    combined=' '.join([parsed.title]+[v for v in fact_values.values() if v])
    parsed.keywords=list(dict.fromkeys(tokens(combined)))[:8] or parsed.keywords
    parsed.patent_queries=list(dict.fromkeys([combined[:100]]+parsed.patent_queries))[:3]
    parsed.market_query=' '.join([parsed.title,*fact_values.values(),'한국 시장 수요 경쟁 제품 가격 지불 의사'])
    query=' '.join(parsed.keywords)+' '+(fact_values['technology'] or parsed.title)
    evidence,patent_search_strategy,search_notices=search_patents(query,parsed.patent_queries)
    patent_data=extract_patents(evidence,demo,client,model)
    market=search_market(parsed,demo,client,os.getenv('MARKET_SEARCH_MODEL','gpt-4.1'),
        os.getenv('MARKET_SEARCH_ENABLED','true').lower()=='true')
    generated=generate(idea,parsed,patent_data,market,demo,client,model)
    score,count,label=enforce_and_score(parsed,generated,patent_data,market,demo=demo)
    review_items=[]
    for item in evidence:
        priority='상세 검토 우선' if item.similarity>=0.75 else '비교 권장' if item.similarity>=0.55 else '참고'
        review_items.append(PatentReviewItem(citation_id=item.citation_id,similarity=item.similarity,
            review_priority=priority,claims_available=bool(item.patent.claims.strip()),matched_section=item.section))
    high_similarity=any(item.similarity>=0.75 for item in evidence)
    patent_review=PatentReview(status=('평가 보류' if not evidence else
        '유사 문헌 상세 검토 권장' if high_similarity else '검색 결과 비교'),
        summary=('검색된 특허가 없습니다. 특허가 없다는 결론이 아니라 검색 근거 부족으로 검토를 보류합니다.' if not evidence else
        '유사도는 검색 순위 참고값입니다. 청구항 대조와 전문가 검토 전에는 신규성·진보성·침해·등록 가능성을 판단할 수 없습니다.'),
        items=review_items,limitations=list(dict.fromkeys([
            '검색 코퍼스와 질의 범위에 한정된 결과이며 특허 전체를 빠짐없이 검색한 것이 아닙니다.',
            '벡터 유사도는 법적 유사성이나 특허 등록 가능성을 뜻하지 않습니다.',
            *(['일부 문헌의 청구항 원문을 확보하지 못해 요약·초록 중심으로 비교했습니다.']
              if any(not row.claims_available for row in review_items) else []),
            '출원 전에는 청구항 단위 선행기술 조사와 변리사 검토가 필요합니다.'])))
    warnings=['사업성 점수는 초기 검토 지표이며 성공 확률이나 법적 판단이 아닙니다.',
        '특허의 기재 효과는 실제 효과가 검증되었다는 의미가 아닙니다.',
        '원문 인용의 존재를 검증하지만 해석의 정확성까지 보장하지 않습니다.',market.warning,*search_notices]
    if len(evidence)<5:
        warnings.append(f'관련성 기준을 충족한 특허가 {len(evidence)}건입니다. 5건을 채우기 위해 무관한 특허를 추가하지 않았습니다.')
    if demo:
        warnings.append('가상 문서·해시 벡터·고정 응답 데모이며 실제 분석·시장 검색·사업성 채점을 수행하지 않았습니다.')
    status='no_results' if not evidence else ('partial' if len(evidence)<5 or market.status!='completed' else 'completed')
    business_plan_draft=build_business_plan(parsed,generated,patent_data,market,evidence,patent_review,
        score,count,label)
    return Report(mode=app.state.mode,status=status,scope=app.state.scope,parsed_idea=parsed,
        patent_data=patent_data,market=market,generated=generated,business_plan_draft=business_plan_draft,
        patent_review=patent_review,evidence=evidence,
        score=score,evaluated_count=count,score_label=label,warnings=warnings,
        metadata={'generated_at':datetime.now(timezone.utc).isoformat(),'prompt_version':'2','rubric_version':'2',
            'llm_model':'fixture' if demo else model,'embedding_model':'hash-demo' if demo else os.getenv('EMBEDDING_MODEL','text-embedding-3-small'),
            'market_model':'none' if demo else os.getenv('MARKET_SEARCH_MODEL','gpt-4.1'),
            'patent_source':app.state.source,'patent_count':str(len(evidence)),
            'patent_search_strategy':patent_search_strategy})

@app.post('/api/analyze',response_model=Report)
async def analyze(idea: AnalysisRequest):
    fingerprint=hashlib.sha256(json.dumps(idea.model_dump(mode='json'),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    if idea.problem is None:
        worker=analyze_sync
        args=(Idea(idea=idea.idea),)
    else:
        parsed=ParsedIdea(title=idea.idea[:60],
            problem=InputFact(value=idea.problem,quote=idea.problem,basis='사용자 확인'),technology=InputFact(value=idea.technology,quote=idea.technology,basis='사용자 확인'),
            customers=InputFact(value=idea.customers,quote=idea.customers,basis='사용자 확인'),difference=InputFact(value=idea.difference,quote=idea.difference,basis='사용자 확인'),
            revenue=InputFact(value=idea.revenue,quote=idea.revenue,basis='사용자 확인'),keywords=[idea.technology],patent_queries=[idea.technology],
            market_query=idea.idea,assumptions=idea.assumptions or ['사용자가 자동 작성 항목을 확인하거나 수정했습니다.'],
            missing_information=idea.missing_information)
        worker=analyze_parsed
        args=(Idea(idea=idea.idea),parsed,app.state.mode=='demo',app.state.client,os.getenv('LLM_MODEL','gpt-4o-mini'))
    async with app.state.analysis_guard:
        current=app.state.analysis_task
        if current is not None and current.done():
            app.state.analysis_task=None
            app.state.analysis_fingerprint=None
            current=None
        if current is not None:
            if fingerprint!=app.state.analysis_fingerprint:
                raise HTTPException(409,'다른 아이디어를 분석 중입니다. 현재 분석이 끝난 뒤 다시 시도해주세요.')
            task=current
        else:
            task=asyncio.create_task(asyncio.to_thread(worker,*args))
            app.state.analysis_task=task
            app.state.analysis_fingerprint=fingerprint
    def clear_completed(done_task):
        if not done_task.cancelled():
            done_task.exception()
        async def clear_state():
            async with app.state.analysis_guard:
                if app.state.analysis_task is done_task:
                    app.state.analysis_task=None
                    app.state.analysis_fingerprint=None
        asyncio.create_task(clear_state())
    task.add_done_callback(clear_completed)
    try:
        return await asyncio.wait_for(asyncio.shield(task),timeout=float(os.getenv('ANALYSIS_TIMEOUT','240')))
    except asyncio.TimeoutError:
        raise HTTPException(504,'분석 시간이 초과되었습니다. 이전 작업 정리 후 재시도해주세요.')
    except KiprisError as exc:
        raise HTTPException(502,str(exc))
    except AnalysisStageError as exc:
        raise HTTPException(502,f"{exc.stage} 단계의 AI 응답을 읽지 못했습니다. 잠시 후 다시 시도해주세요.")
    except ValueError:
        raise HTTPException(502,'구조화 응답 또는 원문 인용 검증에 실패했습니다. 다시 시도해주세요.')
    except Exception:
        raise HTTPException(502,'검색 또는 모델 API 호출에 실패했습니다. 서버 설정을 확인해주세요.')
