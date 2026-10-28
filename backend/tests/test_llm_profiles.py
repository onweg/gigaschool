"""Профиль выбирает сервер; браузер не расширяет модели и адреса API."""
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, build_provider, get_settings
from app.llm.catalog import PRO, REASONING
from app.llm.metered import MeteredProvider
from app.llm.models import ModelRouter
from app.llm.mock import MockProvider

YANDEX = 'gpt://test-folder/yandexgpt/latest'


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for key in ('LLM_PROFILE', 'LLM_PROVIDER', 'LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL',
                'LLM_AUTH_SCHEME', 'LLM_EXTRA_HOSTS', 'LLM_CACHE', 'OPENAI_BASE_URL', 'OPENAI_API_KEY', 'OPENAI_MODEL'):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def yandex_env(monkeypatch):
    monkeypatch.setenv('LLM_PROFILE', 'yandex_dev')
    monkeypatch.setenv('LLM_BASE_URL', 'https://llm.api.cloud.yandex.net/v1')
    monkeypatch.setenv('LLM_API_KEY', 'fake-key')
    monkeypatch.setenv('LLM_MODEL', YANDEX)


def test_yandex_has_one_fixed_model_and_no_cross_profile_choices(monkeypatch):
    yandex_env(monkeypatch)
    provider = build_provider()
    assert provider.auth_scheme == 'Api-Key'
    router = ModelRouter(provider, profile=get_settings().llm_profile)
    assert list(router.options) == [YANDEX]
    assert router.default == YANDEX
    assert router.host == 'llm.api.cloud.yandex.net'
    for model in (PRO, REASONING, 'gpt://another/yandexgpt/latest', 'arbitrary'):
        with pytest.raises(ValueError):
            router.resolve(model)


@pytest.mark.parametrize('model', [PRO, REASONING, 'gpt://folder/yandexgpt-lite/latest', 'arbitrary'])
def test_yandex_profile_refuses_other_models(monkeypatch, model):
    yandex_env(monkeypatch)
    monkeypatch.setenv('LLM_MODEL', model)
    with pytest.raises(ConfigError, match='разрешена только модель'):
        build_provider()


@pytest.mark.parametrize('url', ['https://hub.gigaschool.ru/api/v1/llm/v1', 'https://evil.example/v1',
                                  'https://llm.api.cloud.yandex.net.evil.example/v1', 'http://llm.api.cloud.yandex.net/v1',
                                  'https://llm.api.cloud.yandex.net/another-api'])
def test_yandex_cannot_redirect_key_to_another_api(monkeypatch, url):
    yandex_env(monkeypatch)
    monkeypatch.setenv('LLM_BASE_URL', url)
    with pytest.raises(ConfigError):
        build_provider()


def test_hackathon_remains_default_and_does_not_fall_back_to_yandex(monkeypatch):
    yandex_env(monkeypatch)
    monkeypatch.delenv('LLM_PROFILE')
    assert get_settings().llm_profile == 'hackathon'
    with pytest.raises(ConfigError):
        build_provider()


def test_unknown_profile_refuses_start_even_without_key(monkeypatch):
    monkeypatch.setenv('LLM_PROFILE', 'anything')
    with pytest.raises(ConfigError, match='LLM_PROFILE'):
        build_provider()


def test_wrong_provider_or_missing_model_never_silently_switches_to_mock(monkeypatch):
    yandex_env(monkeypatch)
    monkeypatch.setenv('LLM_PROVIDER', 'unknown')
    with pytest.raises(ConfigError, match='LLM_PROVIDER'):
        build_provider()
    monkeypatch.setenv('LLM_PROVIDER', 'openai_compat')
    monkeypatch.delenv('LLM_MODEL')
    get_settings.cache_clear()
    with pytest.raises(ConfigError, match='не задана модель'):
        build_provider()


def test_yandex_api_catalog_and_session_do_not_allow_profile_override(monkeypatch):
    import app.main as main
    router = ModelRouter(MockProvider(), profile='yandex_dev', configured_model=YANDEX)
    monkeypatch.setattr(main, 'model_router', router)
    monkeypatch.setattr(main, 'provider', MeteredProvider(router))
    client = TestClient(main.app)
    catalog = client.get('/api/models').json()
    assert catalog['profile'] == 'yandex_dev'
    assert catalog['selection_enabled'] is False
    assert [m['id'] for m in catalog['models']] == [YANDEX]
    assert client.get('/api/health').json()['llm_profile'] == 'yandex_dev'
    created = client.post('/api/sessions', json={'text': 'Программа по Python'})
    assert created.status_code == 200, created.text
    assert created.json()['model_id'] == YANDEX
    url = f"/api/sessions/{created.json()['id']}/model"
    assert client.post(url, json={'model_id': PRO}).status_code == 422
    assert client.post(url, json={'model_id': YANDEX, 'profile': 'hackathon'}).status_code == 422
    assert client.post('/api/generate', json={'text': 'Python', 'model_id': PRO}).status_code == 422
    assert client.post('/api/sessions', json={'text': 'Python', 'llm_profile': 'hackathon'}).status_code == 422


def test_make_env_preserves_vps_profile_and_existing_secrets(tmp_path):
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / '.env'
    original = 'LLM_PROFILE=yandex_dev\nLLM_API_KEY=fake-existing-key\nMETRICS_TOKEN=fake-token\n'
    target.write_text(original)
    result = subprocess.run(['make', 'env', f'ENV_FILE={target}', f'PLATFORM_ENV={tmp_path / "missing"}'], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert target.read_text() == original
    assert 'fake-existing-key' not in result.stdout
    assert 'fake-token' not in result.stdout


def test_make_env_initializes_hackathon_only_when_missing(tmp_path):
    root = Path(__file__).resolve().parents[2]
    target, source = tmp_path / '.env', tmp_path / 'ai.env'
    source.write_text('OPENAI_API_KEY="fake-platform-key"\nOPENAI_BASE_URL=https://hub.gigaschool.ru/api/v1/llm/v1\n')
    result = subprocess.run(['make', 'env', f'ENV_FILE={target}', f'PLATFORM_ENV={source}'], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'LLM_PROFILE=hackathon' in target.read_text()
    assert f'OPENAI_MODEL={PRO}' in target.read_text()
    assert 'METRICS_TOKEN=' in target.read_text()
    assert 'fake-platform-key' not in result.stdout
    assert target.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('profile,model,url,count', [
    ('hackathon', PRO, 'https://hub.gigaschool.ru/api/v1/llm/v1', 2),
    ('yandex_dev', YANDEX, 'https://llm.api.cloud.yandex.net/v1', 1),
])
def test_live_profiles_start_without_calling_llm(tmp_path, profile, model, url, count):
    import os
    import sys
    env = {k: v for k, v in os.environ.items() if not k.startswith(('LLM_', 'OPENAI_'))}
    env.update(LLM_PROFILE=profile, OPENAI_MODEL=model, OPENAI_BASE_URL=url,
               OPENAI_API_KEY='fake-key-for-startup-only', DATA_DIR=str(tmp_path), EXPECTED_COUNT=str(count))
    code = '''
import os
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
health = client.get('/api/health')
assert health.status_code == 200, health.text
assert health.json()['llm_profile'] == os.environ['LLM_PROFILE']
assert health.json()['offline_stub'] is False
assert health.json()['llm_host_rule']
catalog = client.get('/api/models').json()
assert len(catalog['models']) == int(os.environ['EXPECTED_COUNT'])
assert catalog['default_id'] == os.environ['OPENAI_MODEL']
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
