type AnswerOption = { label: string; value: string };

/** Варианты — подсказки для ввода. В API по-прежнему уходит обычный текст. */
const ANSWER_OPTIONS: Record<string, AnswerOption[]> = {
  program_type: [
    { label: "Повышение квалификации", value: "Повышение квалификации" },
    { label: "Общеразвивающая", value: "Общеразвивающая программа" },
  ],
  total_hours: [16, 36, 72, 144].map((hours) => ({
    label: `${hours} ч`, value: String(hours),
  })),
  audience: ["Сотрудники компаний", "Госслужащие", "Студенты", "Школьники"].map(
    (value) => ({ label: value, value })
  ),
  entry_level: [
    { label: "С нуля", value: "Без опыта" },
    { label: "Базовый", value: "Базовый уровень подготовки" },
    { label: "Продвинутый", value: "Продвинутый уровень подготовки" },
  ],
  study_form: ["Очная", "Очно-заочная", "Заочная"].map(
    (value) => ({ label: value, value })
  ),
  attestation_form: ["Экзамен", "Зачёт", "Защита проекта", "Собеседование"].map(
    (value) => ({ label: value, value })
  ),
  education_requirements: ["Высшее образование", "Среднее профессиональное образование"].map(
    (value) => ({ label: value, value })
  ),
  uses_dot: [{ label: "С ЭО/ДОТ", value: "Да" }, { label: "Без ЭО/ДОТ", value: "Нет" }],
  assessment_system: ["Зачтено / не зачтено", "Балльная система"].map(
    (value) => ({ label: value, value })
  ),
  learner_artifacts: ["Проект", "Прототип", "Портфолио"].map(
    (value) => ({ label: value, value })
  ),
};

const PLACEHOLDERS: Record<string, string> = {
  program_type: "Выберите тип или напишите свой ответ",
  total_hours: "Свой объём, например 48",
  audience: "Например: аналитики банков, работающие с Excel",
  entry_level: "Например: знают Excel, с Python не работали",
  study_form: "Выберите форму или напишите свой ответ",
  attestation_form: "Например: защита проекта и итоговый тест",
  title: "Если есть точное название для обложки",
  education_requirements: "Укажите только необходимые ограничения",
  desired_outcomes: "Например: анализировать данные и готовить отчёты",
  must_cover: "Например: Python, pandas, SQL",
  normative_requirements: "Название, код или реквизиты документа. Можно приложить файл к запросу",
  uses_dot: "Да или нет",
  assessment_system: "Выберите систему или опишите свою",
  assessment_threshold: "Например: не менее 70 из 100 баллов",
  learner_artifacts: "Например: прототип сервиса и портфолио работ",
};

const normalize = (value: string) => value.trim().toLocaleLowerCase("ru");

export function QuestionAnswer({
  field, question, value, onChange, onSubmit, disabled, multiline = false,
}: {
  field: string;
  question: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  multiline?: boolean;
}) {
  const options = ANSWER_OPTIONS[field] ?? [];
  const labelId = `answer-label-${field}`;

  return (
    <fieldset className="question-answer" disabled={disabled}>
      <legend id={labelId}>{question}</legend>
      {options.length > 0 && (
        <div className="answer-options" role="group" aria-label="Варианты ответа">
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              className="answer-option"
              aria-pressed={normalize(value) === normalize(option.value)}
              onClick={() => onChange(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
      {multiline ? (
        <textarea
          id={`answer-${field}`}
          aria-labelledby={labelId}
          rows={3}
          value={value}
          placeholder={PLACEHOLDERS[field] ?? "Напишите свой ответ"}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : <input
        type="text"
        inputMode={field === "total_hours" ? "decimal" : "text"}
        id={`answer-${field}`}
        aria-labelledby={labelId}
        value={value}
        placeholder={PLACEHOLDERS[field] ?? "Напишите свой ответ"}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.nativeEvent.isComposing && !disabled) {
            event.preventDefault();
            onSubmit();
          }
        }}
      />}
    </fieldset>
  );
}
