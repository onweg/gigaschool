"""Выбор модели не смешивает сессии, стоимость, кэш и готовые материалы."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app, model_router, _model_jobs
from app.llm.base import LLMError, Usage
from app.llm.cache import CachedProvider
from app.llm.metered import MeteredProvider
from app.llm.models import ModelRouter, PRO, REASONING, selected_model, token_cost
from app.llm.openai_compat import OpenAICompatProvider
from app.llm.mock import MockProvider
from app.store import Session, SessionStore, Stage, UsageRecord, store
from conftest import READY

client = TestClient(app)


def ready(model=PRO):
    r = client.post('/api/generate', json={
        'text': 'Повышение квалификации по Python на 72 часа для сотрудников банков, очно-заочно, защита проекта.',
        'answers': READY, 'model_id': model,
    })
    assert r.status_code == 200, r.text
    return r.json()['session_id']


def test_catalog_excludes_embedding_and_secrets():
    data = client.get('/api/models').json()
    assert [m['id'] for m in data['models']] == [PRO, REASONING]
    assert data['offline_stub']
    assert data['models'][0]['input_per_million'] == 73.03
    assert not {'api_key', 'base_url'} & data.keys()


@pytest.mark.parametrize('model', ['gpt://folder/yandexgpt/latest', 'ai-sage/Giga-Embeddings-instruct-3B', 'other-model'])
def test_foreign_models_cannot_enter_router_or_api(model):
    base = OpenAICompatProvider(base_url='https://api.cloud.ru/v1', api_key='fake', model=model)
    with pytest.raises(ValueError, match='разрешены только'):
        ModelRouter(base)
    for endpoint in ['/api/sessions', '/api/generate']:
        response = client.post(endpoint, json={'text': 'Программа по Python', 'model_id': model})
        assert response.status_code == 422, response.text
    session = store.create()
    assert client.post(f'/api/sessions/{session.id}/model', json={'model_id': model}).status_code == 422
    assert client.post('/api/generate', json={'session_id': session.id, 'model_id': model}).status_code == 422


def test_selection_persists_without_changing_program_or_review(tmp_path):
    sid = ready()
    before = client.get(f'/api/sessions/{sid}').json()
    review = client.get(f'/api/sessions/{sid}/review').json()
    response = client.post(f'/api/sessions/{sid}/model', json={'model_id': REASONING})
    assert response.status_code == 200, response.text
    after = response.json()
    assert after['model_id'] == REASONING
    assert after['program'] == before['program']
    assert client.get(f'/api/sessions/{sid}/review').json() == review
    assert after['economics']['cost_rub'] == 0
    local = SessionStore(str(tmp_path))
    local.save(store.get(sid))
    assert SessionStore(str(tmp_path)).get(sid).model_id == REASONING
    assert Session.model_validate({'id': 'a' * 12}).model_id == ''


def test_unknown_models_and_change_during_work_are_rejected():
    r = client.post('/api/sessions', json={'text': 'Python', 'model_id': 'ai-sage/Giga-Embeddings-instruct-3B'})
    assert r.status_code == 422
    sid = ready()
    url = f'/api/sessions/{sid}/model'
    assert client.post(url, json={'model_id': 'arbitrary'}).status_code == 422
    _model_jobs[sid] = 1
    try:
        assert client.post(url, json={'model_id': REASONING}).status_code == 409
    finally:
        del _model_jobs[sid]
    store.get(sid).stage = Stage.CONTENT
    assert client.post(url, json={'model_id': REASONING}).status_code == 409
    assert store.get(sid).model_id == PRO


def test_complete_pipeline_uses_selected_model_without_leaking_context(monkeypatch):
    calls = []

    class Spy(MockProvider):
        async def complete(self, **kwargs):
            calls.append((selected_model.get(), kwargs['system']))
            return await super().complete(**kwargs)

    monkeypatch.setitem(model_router.pool, REASONING, Spy())
    sid = ready(REASONING)
    assert len(calls) > 3
    assert all(m == REASONING for m, _ in calls)
    assert any('SKILL:intake' in s for _, s in calls)
    assert any('SKILL:skeleton' in s for _, s in calls)
    calls.clear()
    review = client.get(f'/api/sessions/{sid}/review').json()
    item = review['items'][0]
    question = client.post(f'/api/sessions/{sid}/review/ask', json={
        'item_id': item['id'], 'revision': item['revision'], 'instruction': 'Как объяснить тему проще?',
    })
    assert question.status_code == 200, question.text
    assert calls and all(m == REASONING for m, _ in calls)
    adapted = client.post(f'/api/sessions/{sid}/adapt', json={'total_hours': 36})
    assert adapted.status_code == 200, adapted.text
    assert adapted.json()['model_id'] == REASONING
    assert all(m == REASONING for m, _ in calls)
    independent = client.post('/api/sessions', json={'text': 'Программа по Python'}).json()
    assert independent['model_id'] == PRO


@pytest.mark.anyio
async def test_parallel_sessions_share_gate_but_keep_models_and_prices():
    base = OpenAICompatProvider(base_url='https://api.cloud.ru/v1', api_key='fake', model=PRO, max_parallel=2)
    router = ModelRouter(base)
    assert router.pool[REASONING]._gate is base._gate
    sent = []

    async def post(payload, headers):
        await asyncio.sleep(.01)
        sent.append(payload['model'])
        return {'choices': [{'message': {'content': '{"ok":true}'}}], 'model': payload['model'],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 200}}

    for p in router.pool.values():
        p._post = post
    metered = MeteredProvider(router)

    async def run(model):
        token = selected_model.set(model)
        try:
            results = await asyncio.gather(*(metered.complete(system='SKILL:test', user='test') for _ in range(3)))
            assert all(r.model == model for r in results)
            assert all(r.usage.cost_rub == token_cost(model, 100, 200) for r in results)
        finally:
            selected_model.reset(token)

    await asyncio.gather(run(PRO), run(REASONING))
    assert sent.count(PRO) == sent.count(REASONING) == 3
    assert base.model == PRO


@pytest.mark.anyio
async def test_separate_caches_and_reasoning_final_answer(tmp_path):
    base = OpenAICompatProvider(base_url='https://api.cloud.ru/v1', api_key='fake', model=PRO)
    router = ModelRouter(CachedProvider(base, tmp_path))
    pro, reason = router.pool[PRO], router.pool[REASONING]
    assert pro._path('s', 'u', True) != reason._path('s', 'u', True)
    assert pro.inner._gate is reason.inner._gate
    payloads = []

    async def post(payload, headers):
        payloads.append(payload)
        return {'choices': [{'message': {'content': '{"answer":"итог"}', 'reasoning_content': 'Не для документа'}}]}

    reason.inner._post = post
    result = await reason.complete(system='s', user='u', max_tokens=1000)
    assert payloads[0]['max_tokens'] == 9192
    assert result.text == '{"answer":"итог"}'

    async def no_final(payload, headers):
        return {'choices': [{'message': {'content': None, 'reasoning_content': 'мысли'}, 'finish_reason': 'length'}]}

    reason.inner._post = no_final
    with pytest.raises(LLMError, match='итоговый текст'):
        await reason.complete(system='s', user='another')


def test_mixed_usage_preserves_historical_cost():
    one = Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000, cost_rub=token_cost(PRO, 1_000_000, 1_000_000))
    two = Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000, cost_rub=token_cost(REASONING, 1_000_000, 1_000_000))
    one += two
    record = UsageRecord()
    record.add(one)
    assert record.cost_rub() == 634.24
    assert record.prompt_tokens == 2_000_000
    record.add(Usage(prompt_tokens=1000, cost_rub=0))
    assert record.cost_rub() == 634.24
