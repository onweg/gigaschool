import { motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";
import { ChatWidget, type Message } from "./components/ChatWidget";
import { HoursTable } from "./components/HoursTable";
import { Kit } from "./components/Kit";
import { AnimatedNumber, Reveal } from "./components/Motion";
import { Report } from "./components/Report";
import { Sidebar, type SectionKey } from "./components/Sidebar";
import { api, type ParsedReference } from "./lib/api";
import type { SessionState } from "./lib/types";

const SAMPLES = [
  {
    n: "16 ч",
    label: "«Первые шаги в Python» для школьников",
    text: "Нужна дополнительная общеразвивающая программа «Первые шаги в Python» для школьников 8 и 9 классов без опыта программирования. Объем 16 часов, занятия очно по 2 часа два раза в неделю. Практики должно быть не меньше половины всех часов. В конце каждый ученик защищает небольшой проект, например простую игру или чат-бота. Нужны итоговый тест на 10 вопросов и слайды для первых двух занятий.",
  },
  {
    n: "72 ч",
    label: "«Анализ данных на Python» для банков",
    text: "Нужна программа повышения квалификации на 72 часа «Анализ данных на Python» для сотрудников региональных отделений банков, которые сейчас работают в Excel. Формат очно-заочный, срок 4 недели. Основные темы это pandas, визуализация, SQL для выгрузки данных и основы статистики. Итоговая аттестация в виде защиты проекта на обезличенных банковских данных. Слайды нужны для модуля про pandas.",
  },
  {
    n: "??",
    label: "Неполный запрос · кибербезопасность",
    text: "Сделайте курс по кибербезопасности для студентов колледжа, примерно на месяц, чтобы после него ребята могли пойти работать в эту сферу.",
  },
  {
    n: "256 ч",
    label: "Стажировка «DevOps от Linux до CI/CD»",
    text: "Нужна программа повышения квалификации в форме стажировки «DevOps-инженер от Linux до CI/CD» на 256 часов. Слушатели проходят стажировку в ИТ-компаниях региона, вся работа самостоятельная под руководством наставника от компании, аудиторных занятий нет. Итоговая аттестация занимает 2 часа и проходит в форме защиты отчёта о стажировке. Срок около двух месяцев, не больше 4 часов в день.",
  },
];

const EXPORTS = [
  { kind: "docx", label: "Программа", ext: "docx" },
  { kind: "json", label: "Комплект", ext: "json" },
  { kind: "pptx", label: "Слайды", ext: "pptx" },
  { kind: "gift", label: "Тесты", ext: "gift" },
  { kind: "csv", label: "Тесты", ext: "csv" },
];

const FIELD_LABEL: Record<string, string> = {
  program_type: "Тип программы",
  total_hours: "Объём, часов",
  audience: "Аудитория",
  entry_level: "Входной уровень",
  study_form: "Форма обучения",
  attestation_form: "Итоговая аттестация",
};

export function App() {
  const [health, setHealth] = useState<{ llm_provider: string; llm_model: string } | null>(null);
  const [session, setSession] = useState<SessionState | null>(null);
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "bot",
      text:
        "Опишите программу свободным текстом: направление, объём часов, аудиторию, форму аттестации. " +
        "Соберу каркас по шаблону АЦР, проверю часы и покрытие компетенций, затем наполню комплект.",
    },
  ]);
  const [draft, setDraft] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [references, setReferences] = useState<ParsedReference[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [busyTopic, setBusyTopic] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(true);
  const [section, setSection] = useState<SectionKey>("program");
  const sectionRefs = useRef<Partial<Record<SectionKey, HTMLDivElement | null>>>({});

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  // Пока идёт генерация содержания, подтягиваем состояние: шаги видно сразу.
  useEffect(() => {
    if (!session || session.stage !== "content") return;
    const timer = setInterval(async () => {
      try {
        const next = await api.state(session.id);
        setSession(next);
        if (next.stage === "ready" || next.stage === "failed") clearInterval(timer);
      } catch {
        clearInterval(timer);
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [session?.id, session?.stage]);

  const program = session?.program ?? null;
  const summary = session?.report_summary;
  const working = busy !== null || session?.stage === "content";

  const availableExports = useMemo(
    () =>
      EXPORTS.filter((item) => {
        if (!program) return false;
        if (item.kind === "pptx") return program.lecture_plans.length > 0;
        if (item.kind === "gift" || item.kind === "csv")
          return program.test_questions.length > 0;
        return true;
      }),
    [program]
  );

  function say(role: Message["role"], text: string) {
    setMessages((prev) => [...prev, { role, text }]);
  }

  function goto(key: SectionKey) {
    setSection(key);
    sectionRefs.current[key]?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function guard<T>(key: string, action: () => Promise<T>) {
    setBusy(key);
    try {
      return await action();
    } catch (error) {
      say("sys", `Ошибка: ${(error as Error).message}`);
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function submitRequest(text: string) {
    if (!text.trim()) return;
    say("user", text);
    setDraft("");
    const next = await guard("create", () =>
      api.createSession(text, references.map((ref) => ref.text))
    );
    if (!next) return;
    setSession(next);
    if (references.length) {
      say("sys", `Референсов принято: ${references.length} — учтена структура, не текст`);
    }
    if (next.questions.length) {
      say(
        "bot",
        `Чтобы не выдумывать параметры, уточню ${next.questions.length} ${
          next.questions.length === 1 ? "момент" : "момента"
        }.`
      );
    } else {
      say("bot", "Параметров достаточно — собираю каркас.");
      await runSkeleton(next.id);
    }
  }

  async function sendAnswers() {
    if (!session) return;
    const filled = Object.fromEntries(
      Object.entries(answers).filter(([, value]) => value.trim())
    );
    if (!Object.keys(filled).length) return;
    say(
      "user",
      Object.entries(filled)
        .map(([field, value]) => `${FIELD_LABEL[field] ?? field}: ${value}`)
        .join(" · ")
    );
    const next = await guard("answers", () => api.answer(session.id, filled));
    if (!next) return;
    setSession(next);
    setAnswers({});
    if (next.missing.length === 0) {
      say("bot", "Спасибо, параметров достаточно. Собираю каркас.");
      await runSkeleton(next.id);
    }
  }

  async function runSkeleton(id: string) {
    const next = await guard("skeleton", () => api.skeleton(id));
    if (!next) return;
    setSession(next);
    setSection("program");
    const report = next.report_summary;
    say(
      "bot",
      `Каркас собран: ${next.program?.modules.length} модулей, ${next.program?.modules.reduce(
        (acc, m) => acc + m.topics.length,
        0
      )} тем, ${next.program?.meta.total_hours} ч. Автопроверка: ошибок ${
        report?.errors ?? 0
      }, предупреждений ${report?.warnings ?? 0}. Проверьте часы и компетенции слева.`
    );
  }

  async function approveAndFill() {
    if (!session) return;
    const approved = await guard("approve", () => api.approve(session.id));
    if (!approved) return;
    setSession(approved);
    say("user", "Каркас утверждаю.");
    const testTotal = /тест[^.]*?(\d+)\s*вопрос/i.exec(String(session.request.raw_text ?? ""));
    const started = await guard("content", () =>
      api.content(session.id, testTotal ? Number(testTotal[1]) : null, [1])
    );
    if (!started) return;
    setSession(started);
    say("bot", "Собираю содержание: темы, тесты с ключами, задания семинаров, слайды.");
  }

  async function regenerate(topicId: string) {
    if (!session) return;
    setBusyTopic(topicId);
    try {
      const next = await api.regenerateTopic(session.id, topicId);
      setSession(next);
      say("bot", "Тема пересобрана, остальной комплект не затронут.");
    } catch (error) {
      say("sys", `Ошибка: ${(error as Error).message}`);
    } finally {
      setBusyTopic(null);
    }
  }

  function reset() {
    setSession(null);
    setMessages(messages.slice(0, 1));
    setAnswers({});
    setReferences([]);
    setSection("program");
  }

  const bind = (key: SectionKey) => (el: HTMLDivElement | null) => {
    sectionRefs.current[key] = el;
  };

  return (
    <div className="shell">
      <Sidebar
        program={program}
        session={session}
        active={section}
        onSelect={goto}
        issues={summary ? summary.errors + summary.warnings : 0}
      />

      <main className="main">
        <div className="topbar">
          <span className="pill">
            <span
              className="dot"
              style={{ color: health ? "var(--green)" : "var(--red)" }}
            />
            {health ? `${health.llm_provider} · ${health.llm_model}` : "API недоступен"}
          </span>
          {summary && (
            <span
              className={`pill ${summary.errors ? "err" : summary.warnings ? "warn" : "accent"}`}
            >
              <span className="dot" />
              автопроверка · {summary.errors} ошибок · {summary.warnings} предупреждений
            </span>
          )}
          <div className="seg">
            <button className="on">RU</button>
            <button disabled>EN</button>
          </div>
        </div>

        <div className="content">
          {!program ? (
            <>
              <h1 className="page-title">Программы обучения</h1>
              <p className="page-lede">
                Агент собирает программу по шаблону Академии цифрового развития и полный
                комплект материалов к ней: карту компетенций, учебный план с точными часами,
                тесты с ключами, задания семинаров и слайды лекций.
              </p>
              <div className="empty">
                <div className="big">Здесь появится собранная программа</div>
                Опишите задачу агенту в чате справа внизу. Часы считает детерминированный
                солвер, нормативную базу подставляет проверенный реестр — модель не
                придумывает ни цифр, ни номеров приказов.
              </div>
            </>
          ) : (
            <>
              <h1 className="page-title">{program.meta.title}</h1>
              <div className="row" style={{ marginBottom: 26 }}>
                <span className="pill accent">
                  {program.meta.program_type === "dpp_pk"
                    ? "ДПП повышения квалификации"
                    : "Общеразвивающая программа"}
                </span>
                <span className="pill">
                  <AnimatedNumber value={program.meta.total_hours} /> ак. ч
                </span>
                <span className="pill">{program.meta.issued_document || "—"}</span>
                {session && <span className="pill mono">{session.id}</span>}
              </div>

              <Reveal>
                <div className="card" ref={bind("program")}>
                  <h3>Паспорт программы</h3>
                  <div className="lede" style={{ marginBottom: 14 }}>
                    <b>Цель.</b> {program.goal}
                  </div>
                  {program.objectives.length > 0 && (
                    <div className="lede">
                      <b>Задачи.</b>{" "}
                      {program.objectives.map((o, i) => (
                        <span key={i}>
                          {i > 0 ? "; " : ""}
                          {o}
                        </span>
                      ))}
                    </div>
                  )}
                  {program.prof_standards.length > 0 && (
                    <div className="row" style={{ marginTop: 16 }}>
                      {program.prof_standards.map((standard) => (
                        <span
                          key={standard.code}
                          className={`pill ${standard.verification === "verified" ? "accent" : "warn"}`}
                          title={standard.note}
                        >
                          <span className="dot" />
                          {standard.code} · {standard.title.slice(0, 44)}
                          {standard.verification === "verified" ? "" : " · требует проверки"}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </Reveal>

              <Reveal delay={0.04}>
                <div className="card" ref={bind("competencies")}>
                  <h3>
                    Карта компетенций <span className="n">{program.outcomes.length} РО</span>
                  </h3>
                  {program.outcomes.map((outcome, i) => {
                    const topics = program.modules
                      .flatMap((m) => m.topics)
                      .filter((t) => t.outcome_ids.includes(outcome.id))
                      .map((t) => t.code);
                    const tests = program.test_questions.filter((q) =>
                      q.outcome_ids.includes(outcome.id)
                    ).length;
                    const seminars = program.seminar_tasks.filter((s) =>
                      s.outcome_ids.includes(outcome.id)
                    ).length;
                    return (
                      <motion.div
                        key={outcome.id}
                        className="outcome-row"
                        initial={{ opacity: 0, x: -6 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ delay: i * 0.04, duration: 0.4 }}
                      >
                        <span className="tag green">{outcome.id}</span>
                        <span style={{ flex: 1 }}>
                          <span className="kind">{outcome.kind}: </span>
                          {outcome.text}
                        </span>
                        <span className="meta">
                          {topics.join(", ") || "—"} · тестов {tests} · заданий {seminars}
                        </span>
                      </motion.div>
                    );
                  })}
                </div>
              </Reveal>

              <Reveal delay={0.08}>
                <div className="card" ref={bind("plan")}>
                  <h3>Учебно-тематический план</h3>
                  <HoursTable
                    program={program}
                    onRegenerate={session?.stage === "ready" ? regenerate : undefined}
                    busyTopic={busyTopic}
                  />
                </div>
              </Reveal>

              <Reveal delay={0.12}>
                <div className="card" ref={bind("report")}>
                  <h3>
                    Отчёт автопроверки
                    {summary && (
                      <span className="n">
                        {summary.errors} ошибок · {summary.warnings} предупреждений
                      </span>
                    )}
                  </h3>
                  <Report findings={session?.report?.findings ?? []} />
                </div>
              </Reveal>

              <div ref={bind("kit")}>
                <Kit program={program} />
              </div>

              <Reveal delay={0.08}>
                <div className="card">
                  <h3>Экономика генерации</h3>
                  <div className="metrics">
                    <div className="metric">
                      <div className="v">
                        <AnimatedNumber
                          value={session?.economics.tokens ?? 0}
                          format={(v) => Math.round(v).toLocaleString("ru")}
                        />
                      </div>
                      <div className="k">токенов</div>
                    </div>
                    <div className="metric">
                      <div className="v">
                        <AnimatedNumber value={session?.economics.cost_rub ?? 0} decimals={2} /> ₽
                      </div>
                      <div className="k">стоимость генерации</div>
                    </div>
                    <div className="metric">
                      <div className="v">
                        <AnimatedNumber value={session?.economics.manual_hours_baseline ?? 0} /> ч
                      </div>
                      <div className="k">ручная сборка программы</div>
                    </div>
                    <div className="metric">
                      <div className="v">
                        <AnimatedNumber value={session?.economics.llm_calls ?? 0} />
                      </div>
                      <div className="k">вызовов модели</div>
                    </div>
                  </div>
                </div>
              </Reveal>

              <Reveal delay={0.1}>
                <div className="card" ref={bind("exports")}>
                  <h3>Действия и выгрузки</h3>
                  <div className="row" style={{ marginBottom: 18 }}>
                    {session?.stage === "skeleton" && (
                      <button
                        className="btn primary"
                        disabled={busy !== null}
                        onClick={() => void approveAndFill()}
                      >
                        {busy ? "…" : "Утвердить каркас и собрать комплект"}
                      </button>
                    )}
                    {session?.stage === "content" && (
                      <span className="pill warn">
                        <span className="dot" /> генерация содержания
                      </span>
                    )}
                    {session?.stage === "ready" && (
                      <span className="pill accent">
                        <span className="dot" /> комплект готов
                      </span>
                    )}
                  </div>
                  <div className="exports">
                    {session &&
                      availableExports.map((item, i) => (
                        <motion.a
                          key={item.kind}
                          href={api.exportUrl(session.id, item.kind)}
                          initial={{ opacity: 0, y: 8 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ delay: i * 0.04, duration: 0.35 }}
                        >
                          {item.label}
                          <span className="ext">.{item.ext}</span>
                        </motion.a>
                      ))}
                  </div>
                </div>
              </Reveal>
            </>
          )}
        </div>
      </main>

      <ChatWidget
        open={chatOpen}
        onOpenChange={setChatOpen}
        session={session}
        messages={messages}
        draft={draft}
        onDraft={setDraft}
        onSubmit={(text) => void submitRequest(text)}
        answers={answers}
        onAnswers={setAnswers}
        onSendAnswers={() => void sendAnswers()}
        references={references}
        onReferences={setReferences}
        samples={SAMPLES}
        onApprove={() => void approveAndFill()}
        exports={availableExports}
        exportUrl={(kind) => api.exportUrl(session?.id ?? "", kind)}
        busy={busy}
        working={working}
        onReset={reset}
      />
    </div>
  );
}
