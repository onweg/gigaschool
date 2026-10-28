"""Разрешённые модели площадки и выбор, изолированный контекстом операции."""
from __future__ import annotations

import copy
from contextvars import ContextVar

from app.llm.base import LLMProvider
from app.llm.cache import CachedProvider
from app.llm.openai_compat import OpenAICompatProvider

from app.llm.catalog import MODELS, PRO, REASONING, is_yandex_model

selected_model: ContextVar[str] = ContextVar('selected_model', default='')


def token_cost(model: str, prompt: int, completion: int) -> float:
    if model.startswith('mock'):
        return 0.0
    if model in MODELS:
        m = MODELS[model]
        return (prompt * m['input_per_million'] + completion * m['output_per_million']) / 1_000_000
    from app.config import get_settings
    s = get_settings()
    return (prompt * s.price_prompt_per_1k + completion * s.price_completion_per_1k) / 1000


class ModelRouter(LLMProvider):
    def __init__(self, base: LLMProvider, *, profile: str = "hackathon", configured_model: str = ""):
        if profile not in ("hackathon", "yandex_dev"):
            raise ValueError("неизвестный профиль моделей")
        self.profile = profile
        self.base = base
        self.offline = base.name.startswith('mock')
        if profile == "yandex_dev":
            self.default = configured_model if self.offline else base.model
            if not is_yandex_model(self.default):
                raise ValueError("yandex_dev: разрешён только настроенный YandexGPT")
            self.options = {self.default: {'id': self.default, 'name': 'YandexGPT',
                'description': 'Единственная модель тестового VPS', 'reasoning': False,
                'input_per_million': None, 'output_per_million': None}}
        else:
            self.default = PRO if self.offline else base.model
            self.options = {k: dict(v) for k, v in MODELS.items()}
        if self.default not in self.options:
            raise ValueError('разрешены только GigaChat 3 Pro и GigaChat 3.5 Reasoning')
        self.pool: dict[str, LLMProvider] = {}
        for model in self.options:
            if self.offline or model == self.default:
                self.pool[model] = base
                continue
            inner = base.inner if isinstance(base, CachedProvider) else base
            if not isinstance(inner, OpenAICompatProvider):
                raise ValueError('выбор моделей поддерживает только настроенный OpenAI-совместимый шлюз')
            # Модель меняется в отдельном объекте. Проверенный адрес, ключ и
            # semaphore остаются общими: лимит площадки один на обе модели.
            clone = copy.copy(inner)
            clone.model = model
            if isinstance(base, CachedProvider):
                cached = copy.copy(base)
                cached.inner = clone
                self.pool[model] = cached
            else:
                self.pool[model] = clone

    def resolve(self, model: str = '') -> str:
        chosen = model or self.default
        if chosen not in self.options:
            raise ValueError('эта модель недоступна; выберите модель из списка')
        return chosen

    @property
    def active_id(self):
        return self.resolve(selected_model.get())

    @property
    def name(self):
        return self.pool[self.active_id].name

    @property
    def model(self):
        return self.pool[self.active_id].model

    @property
    def host(self):
        return getattr(self.base, 'host', None)

    async def complete(self, **kwargs):
        # ContextVar наследуется дочерними задачами и не меняется другой сессией.
        return await self.pool[self.active_id].complete(**kwargs)
