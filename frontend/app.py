import json
import math
import os
from pathlib import Path
import altair as alt
import httpx
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1]/'.env')
API=os.getenv('API_BASE_URL','http://127.0.0.1:8000').rstrip('/')
st.set_page_config(page_title='IdeaLens · 아이디어 특허·사업성 검토',page_icon='💡',layout='wide')
st.markdown("""
<style>
    .block-container {max-width: 1180px; padding-top: 2rem; padding-bottom: 4rem;}
    h1, h2, h3 {letter-spacing: -0.035em;}
    div[data-testid="stMetric"] {background: #f5f7fb; border: 1px solid #e6eaf1; padding: 1rem; border-radius: 14px;}
    div[data-testid="stButton"] button, div[data-testid="stFormSubmitButton"] button {border-radius: 10px; min-height: 2.8rem;}
    div[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 14px;}
</style>
""",unsafe_allow_html=True)
st.title('💡 IdeaLens')
st.caption('아이디어 구체화 → 선행 특허 구성 비교 → 출원 전 검토 신호 → 관련 사업성 검토')
try:
    health=httpx.get(API+'/api/health',timeout=5); health.raise_for_status(); config=health.json()
except (httpx.HTTPError,ValueError):
    st.error('백엔드에 연결할 수 없습니다. README의 FastAPI 실행 명령을 확인해주세요.'); st.stop()
st.info(config['scope'])
if config['mode']=='demo':
    st.warning('데모 모드: 가상 특허와 예시 문구로 흐름을 확인합니다. 시장 검색과 실제 사업성 채점은 수행하지 않습니다.')
else:
    st.caption('입력 원문은 분석에 사용되며 서버에 저장하지 않습니다. 아이디어에서 추출한 검색어는 KIPRIS와 웹 검색으로 전송됩니다.')
    if config.get('patent_source')=='kipris' and not config.get('kipris_key_configured'):
        st.warning('KIPRIS_API_KEY를 기존 .env에 설정한 뒤 서버를 재시작해주세요.')
    if config.get('patent_source')=='chroma' and not config.get('kipris_key_configured'):
        st.warning('기존 Chroma 색인을 우선 사용합니다. 검색 결과가 부족할 때 KIPRIS 보충 검색은 KIPRIS_API_KEY 설정 후 사용할 수 있습니다.')

if 'draft' not in st.session_state: st.session_state.draft=None
if 'report' not in st.session_state: st.session_state.report=None
if 'saved_reports' not in st.session_state: st.session_state.saved_reports=[]
if st.session_state.saved_reports:
    with st.expander(f"내 최근 검토 {len(st.session_state.saved_reports)}건 · 현재 브라우저 세션",expanded=False):
        for index,item in enumerate(st.session_state.saved_reports):
            left,right=st.columns([5,1])
            left.markdown(f"**{item['title']}**  ·  {item['saved_at']}")
            if right.button('열기',key=f"open_saved_{index}"):
                st.session_state.report=item['report']; st.session_state.draft=None; st.rerun()
with st.form('idea'):
    idea=st.text_area('아이디어를 자유롭게 설명해주세요',height=150,max_chars=6000,
        placeholder='예: 전기차 배터리 충전속도를 현재보다 50% 단축하는 급속충전 기술로 전기차 시장을 바꾸고 싶습니다.')
    parse_clicked=st.form_submit_button('아이디어 분석 및 항목 자동 작성',type='primary')
if parse_clicked:
    st.session_state.report=None; st.session_state.draft=None
    if len(idea.strip())<10: st.error('아이디어를 10자 이상 입력해주세요.')
    else:
        with st.spinner('아이디어에서 해결 과제와 기술, 고객, 차별점, 수익 모델을 정리하고 있습니다…'):
            try:
                res=httpx.post(API+'/api/parse-idea',json={'idea':idea},timeout=90); res.raise_for_status()
                st.session_state.draft={'idea':idea,**res.json()}
            except httpx.HTTPStatusError as exc:
                try: detail=exc.response.json().get('detail')
                except ValueError: detail=None
                st.error(detail if isinstance(detail,str) else '아이디어 정리에 실패했습니다. 입력과 서버 설정을 확인해주세요.')
            except (httpx.HTTPError,ValueError): st.error('서버 연결 또는 응답 확인에 실패했습니다.')

if st.session_state.draft:
    draft=st.session_state.draft
    st.subheader('자동 작성된 아이디어 항목')
    st.caption('가설로 작성된 내용은 직접 확인·수정해주세요. 수정한 내용을 바탕으로 특허와 시장을 검색합니다.')
    labels={'problem':'해결 과제','technology':'핵심 기술','customers':'대상 고객','difference':'차별점','revenue':'수익 모델'}
    with st.form('confirm_idea'):
        st.text_area('원래 아이디어',value=draft['idea'],height=90,disabled=True)
        edited={key:st.text_area(label+(' · '+(draft[key] or {}).get('basis','AI 가설')),
                    value=(draft[key] or {}).get('value',''),height=90,key='edit_'+key)
                for key,label in labels.items()}
        analyze_clicked=st.form_submit_button('이 내용으로 특허·시장 조사 시작',type='primary')
    if draft.get('assumptions') or draft.get('missing_information'):
        with st.expander('가정과 추가 확인 과제 보기',expanded=False):
            for assumption in draft.get('assumptions',[]): st.write('가정 · '+assumption)
            for missing in draft.get('missing_information',[]): st.write('확인 필요 · '+missing)
    if analyze_clicked:
        payload={'idea':draft['idea'],**edited,'assumptions':draft.get('assumptions',[]),
            'missing_information':draft.get('missing_information',[])}
        if any(len(v.strip())<2 for v in edited.values()): st.error('5개 항목을 모두 2자 이상 입력해주세요.')
        else:
            st.session_state.report=None
            with st.spinner('특허 검색·데이터화 → 시장 조사 → 사업성 평가 중입니다. 최대 4분 정도 걸릴 수 있습니다…'):
                try:
                    res=httpx.post(API+'/api/analyze',json=payload,timeout=float(os.getenv('ANALYSIS_TIMEOUT','240'))+10); res.raise_for_status()
                    st.session_state.report=res.json()
                except httpx.HTTPStatusError as exc:
                    try: detail=exc.response.json().get('detail')
                    except ValueError: detail=None
                    st.error(detail if isinstance(detail,str) else '분석에 실패했습니다. 입력과 서버 설정을 확인해주세요.')
                except (httpx.HTTPError,ValueError): st.error('서버 연결 또는 응답 확인에 실패했습니다.')

def source_links(ids, report):
    for sid in ids:
        if sid=='I': st.caption('[I] 사용자 아이디어 입력')
        elif sid.startswith('P'):
            for e in report['evidence']:
                if sid==f"P{e['citation_id']}":
                    p=e['patent']
                    if p['url']: st.link_button(f"[{sid}] {p['link_label']}",p['url'])
                    else: st.caption(f'[{sid}] 가상 데모 문서')
        elif sid.startswith('M'):
            for item in report['market']['evidence']:
                if item['id']==sid:
                    for src in item['sources']: st.link_button(f"[{sid}] {src['title']}",src['url'])

if st.session_state.report:
    report=st.session_state.report; parsed=report['parsed_idea']; result=report['generated']
    save_col,export_col=st.columns([1,4])
    if save_col.button('이 검토를 최근 기록에 저장',type='primary'):
        fingerprint=json.dumps(report,ensure_ascii=False,sort_keys=True)
        if not any(json.dumps(item['report'],ensure_ascii=False,sort_keys=True)==fingerprint for item in st.session_state.saved_reports):
            from datetime import datetime
            st.session_state.saved_reports.insert(0,{'title':parsed['title'],'saved_at':datetime.now().strftime('%Y-%m-%d %H:%M'),
                'report':report})
            st.session_state.saved_reports=st.session_state.saved_reports[:10]
            st.success('최근 검토에 저장했습니다. 이 기록은 현재 브라우저 세션에서만 유지됩니다.')
        else: st.info('이미 최근 검토에 저장되어 있습니다.')
    st.divider(); st.subheader('1. 아이디어 요약')
    st.caption(result['summary'])
    strategy=report.get('metadata',{}).get('patent_search_strategy','')
    if strategy=='chroma_only': st.success('기존 Chroma 색인에서 관련 특허가 충분히 검색되어 KIPRIS 보충 호출을 생략했습니다.')
    elif strategy=='chroma_plus_kipris': st.info('기존 색인 결과가 부족해 KIPRIS에서 관련 특허를 보충했고, 검색 결과를 영구 Chroma 색인에 저장했습니다.')
    elif strategy.startswith('chroma_'): st.caption('기존 Chroma 색인을 먼저 검색하고, 부족한 결과를 KIPRIS에서 보충하려 했습니다.')
    labels={'problem':'해결 과제','technology':'핵심 기술','customers':'대상 고객','difference':'차별점','revenue':'수익 모델'}
    summary_rows=[{'항목':'아이디어', '정리 내용':parsed['title'],'작성 기준':'사용자 입력'}]
    for key,label in labels.items():
        fact=parsed[key]
        summary_rows.append({'항목':label,'정리 내용':fact['value'] if fact else '초안 필요',
            '작성 기준':fact.get('basis','확인 필요') if fact else '입력 부족'})
    st.table(pd.DataFrame(summary_rows))
    if parsed['assumptions'] or parsed['missing_information']:
        with st.expander('가정과 추가 확인 과제 보기',expanded=False):
            for assumption in parsed['assumptions']: st.write('가정 · '+assumption)
            for question in parsed['missing_information']: st.write('확인 필요 · '+question)
    st.subheader(f"2. 관련 특허 {len(report['evidence'])} / 5건 및 구조화 데이터")
    if not report['evidence']: st.info('관련성 기준 내에서 특허를 찾지 못했습니다. 관련 특허가 없다는 의미는 아닙니다.')
    elif len(report['evidence'])<5: st.info('관련성이 있는 결과만 표시합니다. 부족한 수를 무관한 문서로 채우지 않습니다.')
    data={p['citation_id']:p for p in report['patent_data']}
    @st.dialog('특허 상세정보', width='large')
    def show_patent_detail(patent, structured, comparison=None):
        st.subheader(patent['title'])
        st.caption(f"출처: {patent['source']} · 출원인: {patent['applicant'] or '정보 없음'} · 공개일: {patent['publication_date'] or '정보 없음'}")
        st.write(f"출원번호: {patent['application_number'] or '정보 없음'}")
        st.write(f"공개번호: {patent['publication_number'] or '정보 없음'} · IPC: {patent['ipc'] or '정보 없음'}")
        if comparison:
            st.markdown('**아이디어와 기술 구성 비교**')
            left,right=st.columns(2)
            left.markdown('유사점'); left.write(comparison['similarity'])
            right.markdown('차이점'); right.write(comparison['difference'])
            st.caption(comparison['uncertainty'])
        st.markdown('**초록**')
        st.write(patent['abstract'])
        if patent['claims']:
            st.markdown('**청구항**')
            st.write(patent['claims'])
        else:
            st.info('확보한 문서는 초록 중심입니다. 청구항은 확인하지 않았습니다.')
        st.markdown('**문서에서 추출한 구조화 정보**')
        fields=[('problem','해결 과제'),('technology','기술 구성'),('application','적용 분야'),('effect','기재된 효과')]
        for key,label in fields:
            fact=structured[key]
            with st.container(border=True):
                st.markdown(f'**{label}**')
                if fact:
                    st.write(fact['value'])
                    st.caption(f"인용 위치: {fact['section']}")
                    st.text(fact['quote'])
                else:
                    st.caption('문서에서 검증된 근거를 확보하지 못했습니다.')
        if structured['limitations']:
            st.markdown('**확인 한계**')
            for limitation in structured['limitations']:
                st.caption('· '+limitation)
        if patent['url']:
            st.link_button('KIPRIS 검색 페이지 열기',patent['url'])
    if report['evidence']:
        st.caption('원 안의 특허를 선택하면 상세정보가 열립니다. 색상과 크기는 검색 우선순위 참고이며 법적 위험도는 아닙니다.')
        nodes=[]; total=len(report['evidence'])
        for index,e in enumerate(report['evidence']):
            angle=-math.pi/2+2*math.pi*index/total
            priority='상세 검토 우선' if e['similarity']>=0.75 else '비교 권장' if e['similarity']>=0.55 else '참고'
            nodes.append({'citation_id':e['citation_id'],'label':f"P{e['citation_id']}",
                'similarity':e['similarity'],'priority':priority,'x':math.cos(angle),'y':math.sin(angle),
                'title':e['patent']['title'],'section':e['section']})
        patent_frame=pd.DataFrame(nodes)
        orbit=pd.DataFrame([{'x':math.cos(angle),'y':math.sin(angle),'angle':angle}
            for angle in [2*math.pi*i/100 for i in range(101)]])
        ring=alt.Chart(orbit).mark_line(color='#cbd5e1',strokeDash=[5,5],strokeWidth=1.5).encode(
            x=alt.X('x:Q',axis=None,scale=alt.Scale(domain=[-1.35,1.35])),
            y=alt.Y('y:Q',axis=None,scale=alt.Scale(domain=[-1.35,1.35])),order='angle:Q')
        pick=alt.selection_point(name='patent_click',fields=['citation_id'],on='click',clear='dblclick')
        circles=alt.Chart(patent_frame).mark_circle(stroke='white',strokeWidth=3,opacity=0.95).encode(
            x=alt.X('x:Q',axis=None,scale=alt.Scale(domain=[-1.35,1.35])),
            y=alt.Y('y:Q',axis=None,scale=alt.Scale(domain=[-1.35,1.35])),
            size=alt.Size('similarity:Q',scale=alt.Scale(domain=[0,1],range=[900,2100]),legend=None),
            color=alt.Color('priority:N',scale=alt.Scale(domain=['상세 검토 우선','비교 권장','참고'],
                range=['#d95050','#e6a23c','#35a47a']),legend=alt.Legend(title='검색 우선순위')),
            tooltip=[alt.Tooltip('label:N',title='특허'),alt.Tooltip('title:N',title='발명명칭'),
                alt.Tooltip('similarity:Q',title='벡터 유사도',format='.3f'),alt.Tooltip('priority:N',title='우선순위'),
                alt.Tooltip('section:N',title='검색 위치')]).add_params(pick)
        labels_chart=alt.Chart(patent_frame).mark_text(color='white',fontSize=13,fontWeight='bold').encode(
            x='x:Q',y='y:Q',text='label:N')
        center=alt.Chart(pd.DataFrame({'x':[0],'y':[0],'label':['아이디어']})).mark_text(color='#475569',fontSize=14,fontWeight='bold').encode(x='x:Q',y='y:Q',text='label:N')
        chart=(ring+circles+labels_chart+center).properties(height=390).configure_view(stroke=None)
        selection=st.altair_chart(chart,use_container_width=True,on_select='rerun',selection_mode='patent_click',key='patent_circle_map')
        chosen=selection.selection.get('patent_click',[]) if selection else []
        if chosen:
            chosen_id=int(chosen[-1]['citation_id'])
            evidence=next((item for item in report['evidence'] if item['citation_id']==chosen_id),None)
            if evidence:
                comparison=next((row for row in result['comparisons'] if row['citation_id']==chosen_id),None)
                show_patent_detail(evidence['patent'],data[chosen_id],comparison)

    st.subheader('3. 출원 전 꼭 확인할 법적 쟁점')
    review=report['patent_review']
    st.warning(review['summary']+' 이 결과는 법률 의견이나 등록 가능성 판정이 아닙니다.')
    legal_checks=[
        ('출원 전에 공개했나요?', '논문·웹사이트·전시·판매·투자 발표 등 공개 시점과 공개 범위를 기록하세요. 출원 전 공개는 신규성 판단에 영향을 줄 수 있습니다. 공지예외 제도에 기대기 전에 요건과 절차를 확인하세요.', '공개 날짜·자료·대상 정리'),
        ('청구항의 핵심 구성이 기존 문헌에 있나요?', '초록의 주제어보다 독립 청구항의 구성요소와 결합 관계를 대조해야 합니다. 유사도 점수만으로 신규성이나 진보성을 결론 내리지 마세요.', '상위 문헌의 독립항 원문 대조'),
        ('특허를 받을 수 있는지와 사업을 실행해도 되는지를 구분했나요?', '자체 아이디어의 특허 가능성과 타인의 유효 특허를 침해하지 않고 실시할 수 있는지는 별개의 검토입니다.', '실시 전 별도 FTO 검토'),
        ('발명자와 권리 귀속이 정리됐나요?', '실제 기술적 기여자, 공동개발자, 회사·학교의 직무발명 및 양도 계약을 출원 전에 확인하세요.', '발명자·계약·권리자 확인'),
        ('출원 국가와 문헌 상태가 맞나요?', '현재 검색은 표시된 데이터 범위에 한정됩니다. 국가별 패밀리, 심사·등록·소멸 상태와 해당 국가의 권리 범위를 별도로 확인하세요.', '국가·패밀리·법적 상태 확인'),
    ]
    legal_cols=st.columns(2)
    for index,(question,detail,action) in enumerate(legal_checks):
        with legal_cols[index%2].container(border=True):
            st.markdown(f"**{index+1}. {question}**")
            st.write(detail)
            st.caption('다음 조치 · '+action)
    st.link_button('특허 등록 요건 안내 · 국가법령정보센터',
        'https://www.law.go.kr/LSW/cgmExpcInfoP.do?cgmExpcDatSeq=6506624&mode=2&ofiClsCd=350137')
    st.link_button('공지예외 등 출원 안내 · 지식재산처',
        'https://www.kipo.go.kr/club/file.do?attachmentId=10367')
    with st.expander('검색 범위와 판단 한계',expanded=False):
        for limitation in review['limitations']: st.caption('· '+limitation)
    st.subheader('4. 시장 조사 근거')
    market=report['market']; st.caption(market['warning'])
    a,b,c=st.columns(3)
    a.metric('출처가 연결된 시장 근거',len(market['evidence']))
    b.metric('참고 출처',len({s['url'] for item in market['evidence'] for s in item['sources']}))
    c.metric('조사 상태',{'completed':'완료','no_results':'결과 없음','failed':'검색 실패','demo':'데모','disabled':'비활성화'}.get(market['status'],market['status']))
    if market['status']=='failed': st.warning('시장 검색 실패 — 특허 결과는 유지하고 시장 관련 평가는 보류했습니다.')
    if not market['evidence']:
        st.info('출처가 연결된 시장 근거가 없습니다. 시장 수요와 수익화 평가는 보류하고 고객 인터뷰로 확인하세요.')
    for item in market['evidence']:
        with st.expander(f"{item['id']} · {item['summary'][:95]}{'…' if len(item['summary'])>95 else ''}",expanded=False):
            st.write(item['summary']); st.caption('조회 시각: '+item['retrieved_at'])
            for source in item['sources']: st.link_button(source['title'],source['url'])

    st.subheader('5. 사업성 평가 · 근거 수준 반영')
    a,b=st.columns(2)
    a.metric(report['score_label'],'평가 보류' if report['score'] is None else f"{report['score']} / 100")
    b.metric('평가 범위',f"5개 중 {report['evaluated_count']}개")
    st.progress(report['evaluated_count']/5,text='근거를 확인한 평가 항목')
    scores=[r for r in result['evaluations'] if r['score'] is not None]
    if scores:
        chart=pd.DataFrame({'점수 (5점 만점)':[r['score'] for r in scores]},index=[r['criterion'] for r in scores])
        st.bar_chart(chart,horizontal=True,x=None,y='점수 (5점 만점)')
    for row in result['evaluations']:
        score='평가 보류' if row['score'] is None else f"{row['score']} / 5"
        with st.container(border=True):
            left,right=st.columns([1,5]); left.metric(row['criterion'],score)
            right.write(row['reason']); right.caption('근거: '+row['evidence'])
            source_links(row['source_ids'],report)
    st.caption('100점 환산은 전체 25점 만점 기준입니다. 보류 항목은 평가 점수로 간주하지 않지만 전체 분모에는 포함되어 잠정 점수를 낮출 수 있습니다. 성공 확률이나 투자 판단이 아닙니다.')
    st.subheader('6. 사업기획 보고서 초안')
    st.caption('특허 검토, 시장 근거, 평가 결과를 하나의 검토 문서로 모았습니다. 초안을 직접 수정하고 미리 본 뒤 Markdown으로 내려받을 수 있습니다.')
    report_key=f"business_plan_{report['metadata']['generated_at']}"
    edit_tab,preview_tab=st.tabs(['초안 편집','보고서 미리보기'])
    with edit_tab:
        st.text_area('사업기획 보고서 내용 (Markdown)',value=report['business_plan_draft'],
            height=620,key=report_key,help='AI 가설과 확인되지 않은 내용은 사실로 바꾸기 전에 근거를 확인해주세요.')
    with preview_tab:
        st.markdown(st.session_state.get(report_key,report['business_plan_draft']))
    st.download_button('사업기획 보고서 초안 내려받기 (Markdown)',
        data=st.session_state.get(report_key,report['business_plan_draft']),
        file_name='idealens-business-plan-draft.md',mime='text/markdown',type='primary')

    st.subheader('7. 다음 검증 액션')
    st.caption('할 일을 체크해 검토 진행 상황을 관리하세요. 체크 상태와 최근 기록은 현재 브라우저 세션에만 저장됩니다.')
    completed=0
    action_cols=st.columns(3)
    for i,item in enumerate(result['improvements']):
        with action_cols[i%3].container(border=True):
            st.markdown(f'**STEP {i+1}**')
            checked=st.checkbox(item,key=f"action_{report['metadata']['generated_at']}_{i}")
            completed+=int(checked)
    st.progress(completed/len(result['improvements']) if result['improvements'] else 0,
        text=f'검증 과제 {completed}/{len(result["improvements"])} 완료')
    st.download_button('전체 분석 데이터 내려받기 (JSON)',data=json.dumps(report,ensure_ascii=False,indent=2),
        file_name='idealens-analysis.json',mime='application/json')
    for warning in report['warnings']: st.caption(warning)
    st.download_button('특허 구조화 데이터 내려받기',data=json.dumps(report['patent_data'],ensure_ascii=False,indent=2),file_name='patent-data.json',mime='application/json')
