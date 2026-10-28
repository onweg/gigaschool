import { motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";
import { ChatWorkspace, type Message } from "./components/ChatWorkspace";
import { ReviewWorkspace } from "./components/ReviewWorkspace";
import { ModelPicker } from "./components/ModelPicker";
import { Icon } from "./components/Icon";
import { PREFERENCE_LABELS } from "./components/RequestPreferences";
import { HoursTable } from "./components/HoursTable";
import { Kit } from "./components/Kit";
import { AnimatedNumber, Reveal } from "./components/Motion";
import { Report } from "./components/Report";
import { SourceCheck } from "./components/SourceCheck";
import { Sidebar, type SectionKey } from "./components/Sidebar";
import { Stats } from "./components/Stats";
import { type AdaptChanges, ApiError, type Health, type ParsedReference, type RefreshResult, type ModelCatalog, api, metricsToken } from "./lib/api";
import type { SessionState } from "./lib/types";

/** Сколько неудачных опросов подряд терпим, прежде чем сказать о потере связи. */
const POLL_FAILURES_TO_REPORT = 4;

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

const STUDY_FORM_LABEL: Record<string, string> = {
  ochnaya: "Очная",
  ochno_zaochnaya: "Очно-заочная",
  zaochnaya: "Заочная",
};

export function App() {
  const restoreVersion = useRef(0);
  const activeSession = useRef<string | null>(null);
  const [loadError, setLoadError] = useState("");
  const [models, setModels] = useState<ModelCatalog | null>(null);
  const [modelError, setModelError] = useState("");
  const [draftModel, setDraftModel] = useState("");
  async function loadModels() {
    try {
      const catalog = await api.models();
      setModels(catalog); setModelError("");
      let saved = "";
      try { saved = localStorage.getItem("acr.model") || ""; } catch { /* private mode */ }
      setDraftModel(current => current || (catalog.models.some(m => m.id === saved) ? saved : catalog.default_id));
    } catch (e) { setModelError(e instanceof ApiError ? e.full : "Не удалось загрузить модели"); }
  }
  useEffect(() => { void loadModels(); }, []);
  const [health, setHealth] = useState<Health | null>(null);
  // Почему агент недоступен — показываем причину, а не просто красную точку.
  const [healthError, setHealthError] = useState<ApiError | null>(null);
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
  const [preferences, setPreferences] = useState<Record<string, string>>({});
  const [references, setReferences] = useState<ParsedReference[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [reviewWorking, setReviewWorking] = useState(false);
  const [busyTopic, setBusyTopic] = useState<string | null>(null);
  /** Действие для кнопки «Повторить» после неудачной сборки. */
  const [retry, setRetry] = useState<(() => void) | null>(null);
  const [section, setSection] = useState<SectionKey>("program");
  const [navigationVersion, setNavigationVersion] = useState(0);

  useEffect(() => {
    api
      .health()
      .then((h) => {
        setHealth(h);
        setHealthError(null);
      })
      .catch((error) => {
        setHealth(null);
        setHealthError(error instanceof ApiError ? error : null);
      });
  }, []);

  // Открытие по прямой ссылке и кнопка «назад» в браузере.
  useEffect(() => {
    const apply = (initial: boolean) => {
      const key = window.location.hash.replace(/^#\/?/, "").split("?")[0] as SectionKey;
      if (!key) { setSection("program"); return; }
      if (!["program", "competencies", "plan", "report", "kit", "exports", "stats", "expert", "review", "approval"].includes(key)) return;
      // Статистика закрыта токеном. Если адрес остался в браузере с прошлого
      // раза, а токена нет, открывать форму ввода вместо главного экрана
      // незачем: человек пришёл за программой, а не за телеметрией. Но по
      // нажатию кнопки форму ввода токена показываем — иначе кнопка мертва.
      if (initial && key === "stats" && !metricsToken.get()) {
        window.location.hash = "";
        setSection("program");
        return;
      }
      setSection(key);
    };
    apply(true);
    const onHash = () => apply(false);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Прямые ссылки на одну программу работают у всех участников и после перезагрузки.
  useEffect(() => {
    let disposed = false;
    const restore = async (initial = false) => {
      let saved = "";
      try { if (initial) saved = localStorage.getItem("acr.session") || ""; } catch { /* private mode */ }
      const id = new URLSearchParams(location.hash.split("?")[1] || "").get("session") || saved;
      if (!id || id === activeSession.current) return;
      const request = ++restoreVersion.current;
      setLoadError("");
      try {
        const next = await api.state(id);
        if (disposed || request !== restoreVersion.current) return;
        activeSession.current = id;
        setSession(next);
        setMessages(prev => [prev[0], { role: "bot", text: "Программа открыта. Можно продолжить работу с материалами." }]);
      } catch (error) {
        if (!disposed && request === restoreVersion.current) setLoadError(error instanceof ApiError ? error.full : "Не удалось открыть программу");
      }
    };
    void restore(true);
    const changed = () => void restore();
    window.addEventListener("hashchange", changed);
    return () => { disposed = true; window.removeEventListener("hashchange", changed); };
  }, []);

  useEffect(() => {
    if (!session) return;
    activeSession.current = session.id;
    try { localStorage.setItem("acr.session", session.id); } catch { /* private mode */ }
    const path = location.hash.split("?")[0] || "#/program";
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    params.set("session", session.id);
    history.replaceState(null, "", `${path}?${params}`);
  }, [session?.id]);

  // Пока идёт генерация содержания, подтягиваем состояние: шаги видно сразу.
  useEffect(() => {
    if (!session || session.stage !== "content") return;
    // Разовый сбой опроса не страшен — сеть моргнула. Но если сервер молчит
    // подряд, раньше генерация просто «висела» без объяснения.
    let failures = 0;
    const timer = setInterval(async () => {
      try {
        const next = await api.state(session.id);
        failures = 0;
        setSession(next);
        if (next.stage === "ready" || next.stage === "failed") clearInterval(timer);
        if (next.stage === "failed") {
          // Фоновая генерация падает молча — сообщаем и даём повторить.
          say("bot", `Сборка комплекта сорвалась: ${next.error || "неизвестная ошибка"}.`);
          setRetry(() => () => void approveAndFill());
        }
      } catch (error) {
        failures += 1;
        if (failures === POLL_FAILURES_TO_REPORT) {
          clearInterval(timer);
          const reason = error instanceof ApiError ? error.full : "сервер не отвечает";
          say("sys", `Потеряна связь с агентом: ${reason}. Генерация на сервере могла продолжиться.`);
          setRetry(() => () => {
            setRetry(null);
            void api.state(session.id).then(setSession).catch(() => undefined);
          });
        }
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [session?.id, session?.stage]);

  const program = session?.program ?? null;
  const summary = session?.report_summary;
  const working = busy !== null || session?.stage === "content" || reviewWorking;

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
    setNavigationVersion((value) => value + 1);
    // Адрес в хэше: страницу статистики нужно уметь открыть по прямой ссылке,
    // а хэш не требует ни роутера, ни настройки сервера.
    window.location.hash = `#/${key}${activeSession.current ? `?session=${activeSession.current}` : ""}`;
    if (key === "stats") {
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
  }

  async function guard<T>(key: string, action: () => Promise<T>) {
    setBusy(key);
    try {
      return await action();
    } catch (error) {
      say("sys", error instanceof ApiError ? error.full : `Ошибка: ${(error as Error).message}`);
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function submitRequest(text: string) {
    if (!text.trim()) return;
    // Программа уже собрана — сообщение в чате это правка, а не новый
    // запрос. Раньше любое сообщение начинало новую программу, и на просьбу
    // «исправь» агент заново спрашивал тип и объём.
    if (session?.program) {
      setDraft("");
      const code = text.match(/(\d+)\s*\.\s*(\d+)/);
      const topic = code
        ? session.program.modules
            .flatMap((m) => m.topics)
            .find((t) => t.code === `${code[1]}.${code[2]}`)
        : undefined;
      if (topic) {
        await regenerate(topic.id, text.trim());
        return;
      }
      say("user", text);
      say(
        "bot",
        code
          ? `Темы ${code[1]}.${code[2]} в плане нет. Номера тем — в разделе «Учебный план».`
          : "Правлю готовую программу по темам: напишите номер темы и что изменить, " +
            "например «тема 2.1: добавить примеры из банков» — пересоберу только её. " +
            "Другая аудитория или объём — «Адаптировать» в разделе «Экспорт», новая программа — кнопка «Новая программа»."
      );
      return;
    }
    say("user", text);
    setDraft("");
    const next = await guard("create", () =>
      api.createSession(text, references.map((ref) => ref.text), preferences, draftModel || undefined)
    );
    if (!next) return;
    setSession(next);
    setPreferences({});
    if (references.length) {
      say("sys", `Референсов принято: ${references.length} — учтена структура, не текст`);
    }
    if (next.questions.length) {
      say(
        "bot",
        "Уточню несколько деталей, которых не хватает в запросе. Дополнительные пожелания можно пропустить."
      );
    } else {
      say("bot", "Параметров достаточно — собираю каркас.");
      await runSkeleton(next.id);
    }
  }

  async function sendAnswers() {
    if (!session) return;
    const filled = { ...preferences, ...Object.fromEntries(
      Object.entries(answers).filter(([, value]) => value.trim())
    ) };
    if (!Object.keys(filled).length) return;
    say(
      "user",
      Object.entries(filled)
        .map(([field, value]) => `${FIELD_LABEL[field] ?? PREFERENCE_LABELS[field] ?? field}: ${value || "не задано"}`)
        .join(" · ")
    );
    const next = await guard("answers", () => api.answer(session.id, filled));
    if (!next) return;
    setSession(next);
    setAnswers({});
    setPreferences({});

    // Непонятый ответ не обрывает диалог: агент переспрашивает с примером
    // и ждёт, пока все параметры не пройдут разбор.
    if (next.unparsed?.length) {
      for (const field of next.unparsed) {
        say("bot", next.reask?.[field] ?? `Не понял ответ на «${field}», уточните.`);
      }
      return;
    }
    if (next.questions.length) {
      say("bot", "Остались уточнения — ответьте на них, и я соберу каркас.");
      return;
    }
    if (next.missing.length === 0) {
      say("bot", "Спасибо, параметров достаточно. Собираю каркас.");
      await runSkeleton(next.id);
    }
  }

  async function runSkeleton(id: string, attempt = 1) {
    const next = await guard("skeleton", () => api.skeleton(id));
    // Сбой сборки не обрывает работу: модель иногда отдаёт структуру,
    // которую не удаётся разобрать, и повтор обычно проходит.
    if (!next) {
      if (attempt < 3) {
        say("bot", `Сборка сорвалась, пробую ещё раз (попытка ${attempt + 1} из 3).`);
        return runSkeleton(id, attempt + 1);
      }
      say(
        "bot",
        "Три попытки подряд не удались. Нажмите «Повторить сборку» или уточните запрос — " +
          "сессия сохранена, ничего не потеряно."
      );
      setRetry(() => () => void runSkeleton(id));
      return;
    }
    setRetry(null);
    setSession(next);
    goto("program");
    const report = next.report_summary;
    say(
      "bot",
      `Каркас собран: ${next.program?.modules.length} модулей, ${next.program?.modules.reduce(
        (acc, m) => acc + m.topics.length,
        0
      )} тем, ${next.program?.meta.total_hours} ч. Автопроверка: ошибок ${
        report?.errors ?? 0
      }, предупреждений ${report?.warnings ?? 0}. Проверьте часы и компетенции в карточке программы ниже.`
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

  // Актуализация: что в программе успело устареть. Отчёт держим рядом с
  // кнопкой — методисту нужно видеть находки, а не искать их в журнале.
  const [refreshResult, setRefreshResult] = useState<RefreshResult | null>(null);

  async function runRefresh() {
    if (!session?.program) return;
    say("user", "Проверь, что в программе устарело");
    const result = await guard("refresh", () => api.refresh(session.id));
    if (!result) return;
    setRefreshResult(result);
    say(
      "bot",
      result.findings.length
        ? `${result.verdict} Нашёл ${result.summary.total}: устарело ${result.summary.outdated}.`
        : `${result.verdict} Устаревшего не нашёл.`
    );
  }

  // Адаптация: готовую программу переносим на новые условия. Форма прячется
  // по умолчанию — это не ежедневное действие, а отдельный сценарий.
  const [adaptOpen, setAdaptOpen] = useState(false);
  const [adaptTo, setAdaptTo] = useState<{ audience: string; hours: string; type: string }>({
    audience: "",
    hours: "",
    type: "",
  });

  async function runAdapt() {
    if (!session?.program) return;
    const changes: AdaptChanges = {};
    if (adaptTo.audience.trim()) changes.audience = adaptTo.audience.trim();
    if (adaptTo.hours.trim()) changes.total_hours = Number(adaptTo.hours);
    if (adaptTo.type) changes.program_type = adaptTo.type as AdaptChanges["program_type"];
    if (!Object.keys(changes).length) return;

    say("user", "Адаптируй программу: " + Object.values(changes).join(", "));
    const next = await guard("adapt", () => api.adapt(session.id, changes));
    if (!next) return;
    setSession(next);
    setAdaptOpen(false);
    setAdaptTo({ audience: "", hours: "", type: "" });
    goto("plan");
    say(
      "bot",
      "Программа адаптирована, исходная сохранена. " + next.changes.slice(0, 4).join("; ")
    );
  }

  async function regenerate(topicId: string, instruction = "", title = "") {
    if (!session) return;
    setBusyTopic(topicId);
    if (instruction) say("user", `Тема ${topicId}: ${instruction}`);
    try {
      const next = await api.regenerateTopic(session.id, topicId, instruction, title);
      setSession(next);
      const topic = next.program?.modules
        .flatMap((m) => m.topics)
        .find((t) => t.id === topicId);
      // До утверждения каркаса содержания ещё нет — пожелание запоминается
      // и применяется при сборке комплекта.
      if (next.stage === "skeleton") {
        say(
          "bot",
          `Запомнил пожелание к теме ${topic?.code ?? topicId}` +
            (topic ? ` «${topic.title}»` : "") +
            ". Учту его, когда соберу комплект после утверждения каркаса."
        );
        return;
      }
      say(
        "bot",
        topic
          ? `Готово: «${topic.title}». ${topic.content?.slice(0, 160) ?? ""}…`
          : "Тема пересобрана, остальной комплект не затронут."
      );
    } catch (error) {
      say("sys", `Ошибка: ${(error as Error).message}`);
    } finally {
      setBusyTopic(null);
    }
  }

  async function chooseModel(modelId: string) {
    setBusy("model");
    try {
      if (session) {
        const next = await api.setModel(session.id, modelId);
        setSession(next);
      }
      setDraftModel(modelId);
      try { localStorage.setItem("acr.model", modelId); } catch { /* private mode */ }
    } finally { setBusy(null); }
  }

  function reset() {
    restoreVersion.current += 1;
    activeSession.current = null;
    try { localStorage.removeItem("acr.session"); } catch { /* private mode */ }
    setLoadError("");
    setSession(null);
    setMessages(messages.slice(0, 1));
    setDraft("");
    setAnswers({});
    setPreferences({});
    setReferences([]);
    setRetry(null);
    setRefreshResult(null);
    setAdaptOpen(false);
    setAdaptTo({ audience: "", hours: "", type: "" });
    goto("program");
  }

  return (
    <div className="shell">
      <Sidebar
        program={program}
        session={session}
        active={section}
        onSelect={goto}
        onReset={reset}
        working={working || busyTopic !== null}
        issues={summary ? summary.errors + summary.warnings : 0}
      />
      <main className="main">
        <header className="workspace-header">
          <div className="workspace-title">
            <ModelPicker catalog={models} value={session?.model_id || draftModel || models?.default_id || ""} disabled={working || busyTopic !== null} onChange={chooseModel} loadError={modelError} onRetry={() => void loadModels()} />
            <span className="workspace-divider">/</span>
            <span className="workspace-name">{section === "stats" ? "Статистика" : program?.meta.title || "Новая программа"}</span>
          </div>
          <span className={`connection ${health ? "online" : "unavailable"}`}
            title={health ? `${health.llm_provider} · ${health.llm_model}` : healthError?.message ?? "Проверяем подключение"}>
            <span className="dot" />
            {health ? (health.offline_stub ? "Офлайн-режим" : "Агент на связи") : healthError ? "Нет связи" : "Подключение…"}
          </span>
        </header>

        {(health?.offline_stub || session?.offline_stub) && (
          <div className="offline-banner" role="status">
            <b>Офлайн-режим.</b> Модель не подключена. Содержание — заглушки, выгрузки помечены как черновик.
          </div>
        )}
        {!health && healthError && (
          <div className="offline-banner error" role="alert">
            <b>{healthError.message}.</b> {healthError.hint}
          </div>
        )}

        {loadError && <div className="offline-banner error" role="alert">{loadError} <button className="btn" onClick={reset}>Начать новую программу</button></div>}
        <div className="review-container" hidden={!["expert", "review", "approval"].includes(section)}>
          <ReviewWorkspace onWorkingChange={setReviewWorking} active={["expert", "review", "approval"].includes(section)} session={session} mode={section === "approval" ? "approval" : section === "review" ? "review" : "expert"} onSession={(next) => { if (activeSession.current === next.id) setSession(next); }} onNavigate={goto} working={working || busyTopic !== null} />
        </div>
        <div className="stats-content" hidden={section !== "stats"}>
          {section === "stats" && <Stats />}
        </div>
        <div className="conversation-container" hidden={["stats", "expert", "review", "approval"].includes(section)}>
          <ChatWorkspace
            session={session}
            messages={messages}
            draft={draft}
            onDraft={setDraft}
            onSubmit={(text) => void submitRequest(text)}
            answers={answers}
            onAnswers={setAnswers}
            preferences={preferences}
            onPreferences={setPreferences}
            onSendAnswers={() => void sendAnswers()}
            references={references}
            onReferences={setReferences}
            onApprove={() => void approveAndFill()}
            exports={availableExports}
            exportUrl={(kind) => api.exportUrl(session?.id ?? "", kind)}
            busy={busy}
            working={working || busyTopic !== null}
            retry={retry}
            section={section}
            navigationVersion={navigationVersion}
            onSelect={goto}
          >
            {program && (
              <section key={session?.id} className="program-artifact" aria-label="Образовательная программа">
                <div className="artifact-heading">
                  <span className="artifact-icon"><Icon name="program" size={22} /></span>
                  <div>
                    <div className="eyebrow">{session?.stage === "ready" ? "Образовательная программа" : "Каркас программы"}</div>
                    <h2>{program.meta.title}</h2>
                  </div>
                  <span className="artifact-hours">{program.meta.total_hours}<small>ак. часов</small></span>
                </div>
                <div className="artifact-meta">
                  <span>{program.meta.program_type === "dpp_pk" ? "Повышение квалификации" : "Общеразвивающая"}</span>
                  <span>{program.modules.length} модулей</span>
                  <span>{program.modules.reduce((n, m) => n + m.topics.length, 0)} тем</span>
                </div>
                <nav className="artifact-tabs" aria-label="Разделы программы">
                  {([
                    ["program", "Обзор"], ["plan", "Учебный план"], ["competencies", "Компетенции"],
                    ["kit", "Материалы"], ["report", "Проверка"], ["exports", "Экспорт"],
                  ] as [SectionKey, string][]).map(([key, label]) => (
                    <button key={key} className={section === key ? "selected" : ""}
                      aria-current={section === key ? "page" : undefined} onClick={() => goto(key)}>
                      {label}{key === "report" && summary && summary.errors + summary.warnings > 0 && (
                        <span className="tab-count">{summary.errors + summary.warnings}</span>
                      )}
                    </button>
                  ))}
                </nav>
                <div className="artifact-body">
              <div hidden={section !== "program"}>
                <Reveal>
                <div className="card">
                  <div className="team-entry"><div><strong>Можно подключить эксперта</strong><span>Проверка материалов и предложения правок — когда нужна помощь коллеги.</span></div><button className="btn" onClick={() => goto("expert")}>Открыть экспертизу <Icon name="arrow" size={14} /></button></div>
                  <h3>Паспорт программы</h3>
                  {program.learning_preferences && Object.values(program.learning_preferences).some((value) => value.length > 0) && (
                    <details className="saved-preferences">
                      <summary>Ваши пожелания к программе</summary>
                      <dl>
                        {Object.entries(program.learning_preferences).filter(([, value]) => value.length > 0).map(([field, value]) => (
                          <div key={field}><dt>{PREFERENCE_LABELS[field] ?? field}</dt><dd>{Array.isArray(value) ? value.join("; ") : value}</dd></div>
                        ))}
                      </dl>
                    </details>
                  )}
                  <dl className="program-facts">
                    <div><dt>Для кого</dt><dd>{program.meta.audience || "—"}</dd></div>
                    <div><dt>Форма обучения</dt><dd>{STUDY_FORM_LABEL[program.meta.study_form] ?? program.meta.study_form ?? "—"}</dd></div>
                    <div><dt>Документ</dt><dd>{program.meta.issued_document || "—"}</dd></div>
                  </dl>
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
              </div>

              <div hidden={section !== "competencies"}>
                <Reveal delay={0.04}>
                <div className="card">
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
              </div>

              <div hidden={section !== "plan"}>
                <Reveal delay={0.08}>
                <div className="card">
                  <h3>Учебно-тематический план</h3>
                  <HoursTable
                    program={program}
                    onRegenerate={session?.stage === "ready" ? regenerate : undefined}
                    busyTopic={busyTopic}
                  />
                </div>
              </Reveal>
              </div>

              <div hidden={section !== "report"}>
                <Reveal delay={0.12}>
                <div className="card">
                  <h3>
                    Отчёт автопроверки
                    {summary && (
                      <span className="n">
                        {summary.errors} ошибок · {summary.warnings} предупреждений
                      </span>
                    )}
                  </h3>
                  <Report findings={session?.report?.findings ?? []} />
                  {session && <SourceCheck session={session} onChange={setSession} />}
                </div>
              </Reveal>
              </div>

              <div hidden={section !== "kit"}>
                {!program.test_questions.length && !program.seminar_tasks.length && !program.lecture_plans.length ? (
                  <div className="material-placeholder"><Icon name="kit" size={32} /><h3>Здесь будет полный комплект</h3><p>Утвердите каркас, чтобы собрать тесты, задания и слайды.</p></div>
                ) : <Kit program={program} />}
              </div>


              <div hidden={section !== "exports"}>
                <Reveal delay={0.1}>
                <div className="card">
                  <h3>Действия и выгрузки</h3>
                  {session?.program && (
                    <div className="adapt-box">
                      <button
                        className="btn ghost"
                        disabled={busy !== null}
                        onClick={() => void runRefresh()}
                        style={{ marginBottom: 12 }}
                      >
                        {busy === "refresh"
                          ? "проверяю актуальность…"
                          : "Проверить, что устарело"}
                      </button>

                      {refreshResult && (
                        <div className="refresh-report">
                          <div className="refresh-verdict">{refreshResult.verdict}</div>
                          {refreshResult.findings.map((f, i) => (
                            <div className={`refresh-item ${f.severity}`} key={i}>
                              <span className="refresh-mark">{f.severity_word}</span>
                              <div>
                                <b>
                                  {f.topic_code ? `${f.topic_code}. ` : ""}
                                  {f.what}
                                </b>
                                {f.why && <div className="refresh-why">{f.why}</div>}
                                {f.replacement && (
                                  <div className="refresh-fix">→ {f.replacement}</div>
                                )}
                              </div>
                            </div>
                          ))}
                          <div className="refresh-note">{refreshResult.disclaimer}</div>
                        </div>
                      )}
                    </div>
                  )}

                  {session?.program && (
                    <div className="adapt-box">
                      {!adaptOpen ? (
                        <button className="btn ghost" onClick={() => setAdaptOpen(true)}>
                          Адаптировать под другую аудиторию или объём
                        </button>
                      ) : (
                        <>
                          <div className="adapt-lede">
                            Переработаем эту программу под новые условия. Текущая
                            останется нетронутой — по ней ещё учатся.
                          </div>
                          <div className="adapt-fields">
                            <label>
                              Аудитория
                              <input
                                value={adaptTo.audience}
                                onChange={(e) =>
                                  setAdaptTo({ ...adaptTo, audience: e.target.value })
                                }
                                placeholder={session.program.meta.audience || "кто учится"}
                              />
                            </label>
                            <label>
                              Объём, ак. ч
                              <input
                                type="number"
                                min={16}
                                step={2}
                                value={adaptTo.hours}
                                onChange={(e) =>
                                  setAdaptTo({ ...adaptTo, hours: e.target.value })
                                }
                                placeholder={String(session.program.meta.total_hours)}
                              />
                            </label>
                            <label>
                              Тип программы
                              <select
                                value={adaptTo.type}
                                onChange={(e) =>
                                  setAdaptTo({ ...adaptTo, type: e.target.value })
                                }
                              >
                                <option value="">не менять</option>
                                <option value="dpp_pk">повышение квалификации</option>
                                <option value="dop_obsh">общеразвивающая</option>
                              </select>
                            </label>
                          </div>
                          <div className="adapt-actions">
                            <button className="btn ghost" onClick={() => setAdaptOpen(false)}>
                              Отмена
                            </button>
                            <button
                              className="btn primary"
                              disabled={busy !== null}
                              onClick={() => void runAdapt()}
                            >
                              {busy === "adapt" ? "перерабатываю…" : "Адаптировать"}
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                  <div className="exports">
                    {session &&
                      availableExports.map((item, i) => (
                        <motion.a
                          key={item.kind}
                          href={api.exportUrl(session.id, item.kind)}
                          // Скачивание без ухода со страницы: иначе срабатывает
                          // «точно уйти?» и файл грузится только после «да».
                          download
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
              </div>

              <details className="economics-details" hidden={section !== "exports"}>
                <summary>Экономика генерации</summary>
              <Reveal delay={0.08}>
                <div className="card">

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
              </details>

                </div>
                <div className="artifact-footer">
                  <span className={summary?.errors ? "artifact-warning" : ""}>
                    <Icon name={summary?.errors ? "report" : "check"} size={15} />
                    {summary?.errors ? `Ошибок: ${summary.errors}` : "Часы и структура проверены"}
                  </span>
                  <button onClick={() => goto(section === "program" ? "plan" : "exports")}>
                    {section === "program" ? "Посмотреть учебный план" : "Действия и экспорт"}<Icon name="arrow" size={15} />
                  </button>
                </div>
              </section>
            )}
          </ChatWorkspace>
        </div>
      </main>
    </div>
  );
}
