import json
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest
from backend.main import app
SAMPLES=json.loads((Path(__file__).resolve().parents[1]/'data/sample_ideas.json').read_text())

def test_idea_auto_fill_then_analyze(monkeypatch):
    monkeypatch.setenv('IDEALENS_MODE','demo')
    with TestClient(app) as client:
        def get(url,**kwargs): return client.get('/api/health')
        def post(url,**kwargs): return client.post(url.replace('http://127.0.0.1:8000',''),json=kwargs['json'])
        with patch('httpx.get',get),patch('httpx.post',post):
            ui=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'frontend/app.py')).run()
            assert not ui.exception
            assert len(ui.text_area)==1
            ui.text_area[0].set_value('토양 수분 센서로 화분 상태를 측정하고 자동 급수 펌프를 제어하는 아이디어입니다.')
            ui.button[0].click().run(timeout=20)
            assert not ui.exception
            assert len(ui.text_area)==7
            assert all(widget.value.strip() for widget in ui.text_area[2:])
            assert any(item.label=='가정과 추가 확인 과제 보기' for item in ui.expander)
            ui.button[1].click().run(timeout=20)
            assert not ui.exception
            assert len(ui.get('vega_lite_chart'))==1
            assert len(ui.get('table'))==1
            assert any(m.label=='평가 보류' and m.value=='평가 보류' for m in ui.metric)
            assert any('사업성 평가' in h.value for h in ui.subheader)

def test_business_score_chart_renders_with_scores(monkeypatch):
    monkeypatch.setenv('IDEALENS_MODE','demo')
    with TestClient(app) as client:
        report=client.post('/api/analyze',json=SAMPLES[0]).json()
    report['score']=60
    report['evaluated_count']=5
    report['score_label']='종합 점수'
    for row in report['generated']['evaluations']: row['score']=3
    ui=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'frontend/app.py'))
    ui.session_state['report']=report
    ui.run()
    assert not ui.exception
