"""Необязательные пожелания методиста, сохраняемые вместе с программой."""
from pydantic import BaseModel, Field


class LearningPreferences(BaseModel):
    education_requirements: str = ""
    desired_outcomes: list[str] = Field(default_factory=list)
    must_cover: list[str] = Field(default_factory=list)
    normative_requirements: str = ""
    assessment_system: str = ""
    assessment_threshold: str = ""
    learner_artifacts: list[str] = Field(default_factory=list)

    def instructions(self) -> str:
        labels = {
            "education_requirements": "Требования к образованию слушателей",
            "desired_outcomes": "Желаемые результаты обучения",
            "must_cover": "обязательно раскрыть темы, названные методистом",
            "normative_requirements": "Нормативные пожелания методиста (требуют проверки, не считать подтверждёнными)",
            "assessment_system": "Система оценивания",
            "assessment_threshold": "Порог и шкала оценивания",
            "learner_artifacts": "Что слушатель должен создать к концу обучения",
        }
        return "\n".join(
            f"{label}: {'; '.join(value) if isinstance(value, list) else value}"
            for field, label in labels.items()
            if (value := getattr(self, field))
        )
