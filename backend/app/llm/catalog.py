"""Каталог хакатона и проверка единственной серверной модели тестового VPS."""
import re

YANDEX_HOST = "llm.api.cloud.yandex.net"


def is_yandex_model(model: str) -> bool:
    return re.fullmatch(r"gpt://[A-Za-z0-9_-]+/yandexgpt/latest", model) is not None


PRO = 'GigaChat/GigaChat-3-Pro'
REASONING = 'ai-sage/GigaChat3.5-432B-A28B-Reasoning'
# Тарифы каталога площадки, предоставленные пользователем 28.10.2026, ₽/1 млн.
MODELS = {
    PRO: {'id': PRO, 'name': 'GigaChat 3 Pro', 'description': 'Быстрая генерация программ и повседневные правки',
          'reasoning': False, 'input_per_million': 73.03, 'output_per_million': 176.39},
    REASONING: {'id': REASONING, 'name': 'GigaChat 3.5 Reasoning', 'description': 'С рассуждениями — для сложных задач и внимательной проверки',
                'reasoning': True, 'input_per_million': 96.22, 'output_per_million': 288.60},
}
