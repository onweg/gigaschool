"""Офлайн-провайдер для демо и тестов.

Нужен по двум причинам: показ не должен зависеть от сети и квот, а валидаторы
и рендереры надо тестировать без обращения к модели. Возвращает структурно
корректные ответы по маркеру `SKILL:<name>` в системном промпте.
"""
from __future__ import annotations

import asyncio
import json
import re

from app.config import get_settings
from app.llm.base import Completion, LLMProvider, Usage

_SKILL = re.compile(r"SKILL:([a-z_]+)")


class MockProvider(LLMProvider):
    name = "mock"
    model = "mock-offline"

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 4000,
        json_mode: bool = False,
    ) -> Completion:
        latency = get_settings().mock_latency_ms
        if latency:
            await asyncio.sleep(latency / 1000)
        match = _SKILL.search(system)
        skill = match.group(1) if match else "unknown"
        payload = _DISPATCH.get(skill, lambda _: {"error": f"mock: нет заготовки для {skill}"})(user)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return Completion(
            text=text,
            usage=Usage(prompt_tokens=len(system + user) // 4, completion_tokens=len(text) // 4),
            model=self.model,
        )


# --------------------------------------------------------------------------- #
def _mock_intake(user: str) -> dict:
    """Нейтральный ответ: заглушка НЕ разбирает запрос методиста.

    Раньше здесь стояли правила вида «есть слово „школьник“ — значит
    общеразвивающая программа». Выглядело это как подстройка под контрольные
    примеры, и справедливо: заглушка знала правильные ответы на запросы кейса.

    Теперь она возвращает только то, что не зависит от текста. Тип программы,
    объём, форму обучения и расписание извлекает `pipeline/intake.py` —
    разбором самого запроса, одинаково и на заглушке, и на живой модели.
    Чего в запросе нет — агент спросит у методиста.

    Аудиторию, входной уровень и форму аттестации заглушка тоже не знает и
    честно возвращает null. Раньше тут стояли «слушатели по описанию
    запроса» и «защита проекта» — то есть заглушка додумывала ровно то, за
    что агент не должен браться без методиста.
    """
    return {
        "title": "Программа по запросу методиста",
        "direction": "ИТ",
        "audience": None,
        "entry_level": None,
        "attestation_form": None,
        "constraints": [],
    }


def _mock_skeleton(user: str) -> dict:
    return {
        "goal": "Сформировать у слушателей практические навыки по заявленному направлению.",
        "objectives": [
            "дать системное представление о предметной области",
            "отработать ключевые инструменты на практических задачах",
            "подготовить слушателей к итоговой аттестации",
        ],
        "annotation": "Программа построена на практических задачах предметной области.",
        "relevance": "Направление входит в число наиболее востребованных на рынке труда.",
        "entry_requirements": "Базовая компьютерная грамотность.",
        "outcomes": [
            {"id": "RO-1", "kind": "знание", "text": "основные понятия и инструменты направления"},
            {"id": "RO-2", "kind": "умение", "text": "применять инструменты для типовых задач"},
            {"id": "RO-3", "kind": "навык", "text": "самостоятельно выполнять итоговый проект"},
        ],
        "competencies": [
            {
                "id": "PK-1",
                "title": "Выполнение типовых задач направления",
                "activity": "Профессиональная деятельность по направлению программы",
                "outcome_ids": ["RO-1", "RO-2", "RO-3"],
            }
        ],
        "modules": [
            {
                "index": 1,
                "title": "Введение и базовые инструменты",
                "weight": 3,
                "mix": {"lectures": 2, "practice": 2, "labs": 0, "self_study": 1},
                "assessment_form": "Текущий контроль: тестирование",
                "outcome_ids": ["RO-1"],
                "topics": [
                    {"title": "Обзор направления и инструментов", "weight": 1, "outcome_ids": ["RO-1"]},
                    {"title": "Базовые конструкции и первые задачи", "weight": 2, "outcome_ids": ["RO-1", "RO-2"]},
                ],
            },
            {
                "index": 2,
                "title": "Практическое применение",
                "weight": 4,
                "mix": {"lectures": 1, "practice": 3, "labs": 0, "self_study": 1},
                "assessment_form": "Текущий контроль: практическая работа",
                "outcome_ids": ["RO-2"],
                "topics": [
                    {"title": "Типовые задачи предметной области", "weight": 2, "outcome_ids": ["RO-2"]},
                    {"title": "Разбор кейсов и отладка решений", "weight": 2, "outcome_ids": ["RO-2", "RO-3"]},
                ],
            },
            {
                "index": 3,
                "title": "Итоговый проект",
                "weight": 2,
                "mix": {"lectures": 1, "practice": 3, "labs": 0, "self_study": 2},
                "assessment_form": "",
                "outcome_ids": ["RO-3"],
                "topics": [
                    {"title": "Проектирование и сборка решения", "weight": 1, "outcome_ids": ["RO-3"]},
                    {"title": "Подготовка защиты проекта", "weight": 1, "outcome_ids": ["RO-3"]},
                ],
            },
        ],
        "final_attestation": {
            "form": "защита проекта",
            "description": "Слушатель защищает проект, выполненный по материалам программы.",
            "criteria": [
                "работоспособность решения",
                "соответствие требованиям задания",
                "качество защиты и ответов на вопросы",
            ],
        },
        "sources": [
            {"kind": "main", "citation": "Официальная документация используемых инструментов", "url": None}
        ],
    }


def _mock_topic_content(user: str) -> dict:
    return {
        "content": "Ключевые понятия темы, разбор примеров, типовые ошибки.",
        "activities": "Практическое занятие: выполнение задания по теме с разбором результата.",
    }


def _mock_tests(user: str) -> dict:
    # Квоту заглушка обязана соблюдать: иначе прогон по чек-листам кейса
    # («итоговый тест на 10 вопросов») проваливается на ровном месте.
    match = re.search(r"Сколько вопросов:\s*(\d+)", user)
    count = int(match.group(1)) if match else 1
    return {
        "questions": [
            {
                "stem": f"Что из перечисленного относится к теме занятия? (вопрос {i + 1})",
                "options": ["Верный вариант", "Неверный вариант 1", "Неверный вариант 2", "Неверный вариант 3"],
                "correct": [0],
                "explanation": "Проверяется базовое понятие темы.",
                "difficulty": "easy",
            }
            for i in range(count)
        ]
    }


def _mock_seminars(user: str) -> dict:
    return {
        "tasks": [
            {
                "title": "Практическое задание по теме",
                "statement": "Выполните задание на материале темы и оформите результат.",
                "deliverable": "Файл с решением и краткий отчёт",
                "criteria": ["решение работает", "оформление соответствует требованиям"],
            }
        ]
    }


def _mock_lecture(user: str) -> dict:
    return {
        "objectives": ["разобрать ключевые понятия темы"],
        "timing": ["введение — 5 мин", "основная часть — 60 мин", "итоги — 10 мин"],
        "slides": [
            {"title": "Тема занятия", "bullets": ["план занятия", "зачем это нужно"], "notes": "Вступление."},
            {"title": "Ключевые понятия", "bullets": ["понятие 1", "понятие 2"], "notes": "Разбор."},
            {"title": "Практика", "bullets": ["задание", "критерии"], "notes": "Переход к практике."},
        ],
    }


def _mock_adapt(user: str) -> dict:
    """Адаптация возвращает ту же структуру, что и каркас, плюс список правок.

    Заглушка не разбирает, что именно просили изменить, — параметры новой
    программы приходят из запроса методиста, а не от модели.
    """
    data = _mock_skeleton(user)
    data["changes"] = [
        "структура сохранена, часы пересчитаны солвером",
        "формулировки приведены под новую аудиторию",
    ]
    return data


def _mock_refresh(user: str) -> dict:
    """Заглушка актуализации: структура ответа без разбора содержания.

    Что устарело, знает только живая модель — заглушка не делает вид, что
    умеет это определять, иначе получится шпаргалка под контрольные примеры.
    """
    return {
        "findings": [
            {
                "topic_code": "",
                "what": "версии инструментов в программе",
                "why": "офлайн-заглушка не проверяет актуальность",
                "replacement": "запустите на живой модели",
                "severity": "aging",
            }
        ],
        "verdict": "офлайн-заглушка: проверка актуальности требует живой модели",
    }


def _mock_attestation_criteria(user: str) -> dict:
    """Структурная заготовка: критерии придумывает живая модель."""
    return {
        "criteria": [
            "работа выполнена самостоятельно и соответствует заданию",
            "результат воспроизводится по приложенному описанию",
            "слушатель отвечает на вопросы по содержанию работы",
        ]
    }


_DISPATCH = {
    "intake": _mock_intake,
    "attestation_criteria": _mock_attestation_criteria,
    "refresh": _mock_refresh,
    "skeleton": _mock_skeleton,
    "adapt": _mock_adapt,
    "topic_content": _mock_topic_content,
    "tests": _mock_tests,
    "seminars": _mock_seminars,
    "lecture": _mock_lecture,
}
