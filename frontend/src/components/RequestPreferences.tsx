import { useState } from "react";
import { Icon } from "./Icon";
import { QuestionAnswer } from "./QuestionAnswer";

export const PREFERENCE_LABELS: Record<string, string> = {
  title: "Название программы",
  education_requirements: "Требования к образованию",
  desired_outcomes: "Желаемые навыки",
  must_cover: "Обязательные темы",
  normative_requirements: "Нормативные требования",
  uses_dot: "Электронное обучение и ДОТ",
  assessment_system: "Система оценивания",
  assessment_threshold: "Порог и шкала",
  learner_artifacts: "Результат работы слушателя",
};

const GROUPS = [
  { title: "Название и слушатели", fields: ["title", "education_requirements"] },
  { title: "Темы и результаты", fields: ["desired_outcomes", "must_cover", "learner_artifacts"] },
  { title: "Формат и оценка", fields: ["uses_dot", "assessment_system", "assessment_threshold"] },
  { title: "Нормативная база", fields: ["normative_requirements"] },
];
const QUESTIONS: Record<string, string> = {
  title: "Есть утверждённое название программы?",
  education_requirements: "Есть требования к образованию слушателей?",
  desired_outcomes: "Чему важно научиться?",
  must_cover: "Какие темы обязательно включить?",
  learner_artifacts: "Что слушатель должен создать к концу обучения?",
  uses_dot: "Использовать электронное обучение и дистанционные технологии?",
  assessment_system: "Как оценивать результаты?",
  assessment_threshold: "Какой проходной порог и шкала?",
  normative_requirements: "Есть конкретный профстандарт или другой документ?",
};
const MULTILINE = new Set(["desired_outcomes", "must_cover", "normative_requirements", "learner_artifacts"]);

export function RequestPreferences({ values, request, onChange, disabled }: {
  values: Record<string, string>;
  request?: Record<string, unknown>;
  onChange: (values: Record<string, string>) => void;
  disabled: boolean;
}) {
  const [active, setActive] = useState(0);
  function value(field: string): string {
    if (field in values) return values[field];
    const saved = request?.[field];
    if (Array.isArray(saved)) return saved.join("\n");
    if (typeof saved === "boolean") return saved ? "Да" : "Нет";
    return typeof saved === "string" ? saved : "";
  }
  const count = Object.keys(values).filter((field) => values[field].trim()).length;
  const fields = GROUPS[active].fields.filter((field) =>
    field !== "assessment_threshold" || /балл|points/i.test(value("assessment_system")) || value(field)
  );

  return (
    <details className="request-preferences">
      <summary><Icon name="plus" size={16} /><span>Есть особые пожелания?</span><small>{count ? `Добавлено: ${count}` : "Необязательно"}</small></summary>
      <div className="preferences-content">
        <p>Уточните только то, что важно. Остальное агент предложит в каркасе — вы сможете проверить его перед сборкой материалов.</p>
        <div className="preference-groups" role="group" aria-label="Группы пожеланий">
          {GROUPS.map((group, index) => (
            <button key={group.title} type="button" aria-pressed={active === index} onClick={() => setActive(index)}>
              {group.title}
            </button>
          ))}
        </div>
        <div className="preference-fields">
          {fields.map((field) => (
            <QuestionAnswer key={field} field={field} question={QUESTIONS[field]} value={value(field)}
              disabled={disabled} multiline={MULTILINE.has(field)} onSubmit={() => undefined}
              onChange={(next) => onChange({ ...values, [field]: next,
                ...(field === "assessment_system" && !/балл|points/i.test(next) ? { assessment_threshold: "" } : {}),
              })} />
          ))}
        </div>
        {active === 1 && <p className="preference-note">Достаточно одного-двух пожеланий. Полный список компетенций составит агент.</p>}
        {active === 3 && <p className="preference-note">Документы не обязательны для старта. Указанные реквизиты требуют проверки методистом.</p>}
      </div>
    </details>
  );
}
