"""Пожелания добровольны, но принятые значения не теряются в конвейере."""
import io
import json

from docx import Document
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.llm.base import Completion, LLMProvider
from app.llm.mock import MockProvider
from app.pipeline.adapt import build_target_request
from app.pipeline.content import fill_topic_content, generate_tests, generate_seminars
from app.pipeline.intake import apply_answers, run_intake
from app.pipeline.skeleton import build_skeleton
from app.schemas.request import ProgramRequest
from app.schemas.ir import ProgramType, StudyForm

client = TestClient(app)
FULL = 'Повышение квалификации по Python на 72 часа для сотрудников банков без опыта, очно-заочно. Итоговая аттестация — защита проекта.'
PREFERENCES = {
    'title': 'Python: точное название на обложке',
    'education_requirements': 'Высшее образование',
    'desired_outcomes': 'Автоматизировать банковские отчёты; проверять качество данных',
    'must_cover': 'pandas, SQL\nвизуализация',
    'normative_requirements': 'Профстандарт 06.001; внутренний регламент X-123',
    'assessment_system': 'Балльная система',
    'assessment_threshold': 'Не менее 70 из 100 баллов',
    'learner_artifacts': 'Прототип отчёта; портфолио работ',
    'uses_dot': 'Нет',
}


def ready_request():
    return ProgramRequest(program_type=ProgramType.DPP_PK, program_type_confidence=1,
        total_hours=72, audience='сотрудники банков', entry_level='базовый',
        study_form=StudyForm.PART_TIME, attestation_form='защита проекта')


def test_optional_preferences_do_not_become_required():
    request = ready_request()
    assert request.ready()
    assert not request.education_requirements and not request.desired_outcomes
    assert apply_answers(request, PREFERENCES) == []
    assert request.ready()
    assert request.must_cover == ['pandas', 'SQL', 'визуализация']
    assert request.desired_outcomes == ['Автоматизировать банковские отчёты', 'проверять качество данных']
    assert request.learner_artifacts == ['Прототип отчёта', 'портфолио работ']
    assert request.deliverables == []  # Материалы агента не смешиваются с работами слушателя.
    assert request.uses_dot is False
    apply_answers(request, {'must_cover': 'SQL;SQL', 'assessment_threshold': '', 'title': ''})
    assert request.must_cover == ['SQL']
    assert request.assessment_threshold == '' and request.title is None


def test_sixth_required_question_is_not_lost():
    first = client.post('/api/sessions', json={'text': 'Нужна программа'}).json()
    assert len(first['missing']) == 6
    assert len(first['questions']) <= 5
    sid = first['id']
    second = client.post(f'/api/sessions/{sid}/answers', json={'answers': {
        'program_type':'повышение квалификации', 'total_hours':'72', 'audience':'сотрудники банков',
        'entry_level':'базовый', 'study_form':'очно-заочная',
    }}).json()
    assert second['missing'] == ['attestation_form']
    assert [q['field'] for q in second['questions']] == ['attestation_form']
    third = client.post(f'/api/sessions/{sid}/answers', json={'answers': {'attestation_form':'защита проекта'}}).json()
    assert not third['missing'] and not third['questions']


def test_create_accepts_optional_details_and_skeleton_exports_them():
    first = client.post('/api/sessions', json={'text': FULL, 'answers': PREFERENCES})
    assert first.status_code == 200, first.text
    state = first.json()
    assert not state['questions']
    assert state['request']['title'] == PREFERENCES['title']
    response = client.post(f"/api/sessions/{state['id']}/skeleton")
    assert response.status_code == 200, response.text
    program = response.json()['program']
    assert program['meta']['title'] == PREFERENCES['title']
    assert program['meta']['uses_dot'] is False
    assert PREFERENCES['education_requirements'] in program['entry_requirements']
    assert program['learning_preferences']['assessment_threshold'] == PREFERENCES['assessment_threshold']
    assert '06.001' in [s['code'] for s in program['prof_standards']]
    assert not any('X-123' in json.dumps(a, ensure_ascii=False) for a in program['legal_acts'])
    report = response.json()['report']['findings']
    assert any(f['code'] == 'NORMATIVE_REQUIREMENTS_REVIEW' and f['severity'] == 'warning' for f in report)
    exported = client.get(f"/api/sessions/{state['id']}/export/docx")
    assert exported.status_code == 200
    document = Document(io.BytesIO(exported.content))
    text = '\n'.join(p.text for p in document.paragraphs)
    for value in [PREFERENCES['title'], PREFERENCES['education_requirements'], PREFERENCES['assessment_system'], PREFERENCES['assessment_threshold'], 'Прототип отчёта', 'портфолио работ']:
        assert value in text


@pytest.mark.anyio
async def test_preferences_reach_generation_and_survive_adaptation():
    seen = []
    class Spy(MockProvider):
        async def complete(self, *, system, user, **kwargs):
            seen.append((system, user))
            return await super().complete(system=system, user=user, **kwargs)
    request = ready_request()
    apply_answers(request, PREFERENCES)
    provider = Spy()
    program, _ = await build_skeleton(provider, request)
    module, topic = program.modules[0], program.modules[0].topics[0]
    await fill_topic_content(provider, program, module, topic)
    await generate_tests(provider, program, [topic])
    await generate_seminars(provider, program, [topic])
    for _, prompt in seen:
        for value in ['Автоматизировать банковские отчёты', 'SQL', 'Прототип отчёта', '70 из 100 баллов']:
            assert value in prompt
    adapted = build_target_request(program, {'total_hours':36})
    assert adapted.learner_artifacts == request.learner_artifacts
    assert adapted.assessment_threshold == request.assessment_threshold
    assert adapted.education_requirements == request.education_requirements


@pytest.mark.anyio
async def test_intake_keeps_only_explicit_optional_details():
    class Guessing(LLMProvider):
        name = model = 'test'
        async def complete(self, **kwargs):
            return Completion(text=json.dumps({
                'education_requirements':'Высшее образование',
                'desired_outcomes':['автоматизировать отчёты', 'управлять командой'],
                'normative_requirements':'Приказ 123456',
                'assessment_system':'балльная система',
                'assessment_threshold':'70 из 100 баллов',
                'learner_artifacts':['прототип отчёта','мобильное приложение'],
            }, ensure_ascii=False))
    result, _ = await run_intake(Guessing(), FULL + ' Нужно автоматизировать отчёты; итог — прототип отчёта. Оценка: балльная система, порог 70 из 100 баллов.')
    assert result.request.education_requirements == ''
    assert result.request.normative_requirements == ''
    assert result.request.desired_outcomes == ['автоматизировать отчёты']
    assert result.request.learner_artifacts == ['прототип отчёта']
    assert result.request.assessment_threshold == '70 из 100 баллов'
    assert not any(q.field in PREFERENCES for q in result.questions)
