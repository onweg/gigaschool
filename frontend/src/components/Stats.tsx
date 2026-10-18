import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useState } from "react";
import { ApiError, DEVICE_ID, metrics, metricsToken, type DayPoint, type FindingStat, type MetricsOverview, type RequestMix, type SessionDetail, type SessionRow, type SkillStat } from "../lib/api";
import { DaysChart, FunnelBars, MiniBars, SERIES } from "./Charts";
import { formatDuration } from "./Motion";

const STAGE_LABEL: Record<string, string> = {
  created: "Запрос принят",
  clarifying: "Заданы уточнения",
  skeleton: "Каркас собран",
  approved: "Каркас утверждён",
  content: "Запущен комплект",
  ready: "Комплект готов",
  failed: "Сбой",
};

const PHASE_LABEL: Record<string, string> = {
  skeleton: "Каркас",
  content: "Комплект",
  regenerate: "Доработка темы",
};

const WINDOWS: { label: string; days?: number }[] = [
  { label: "7 дней", days: 7 },
  { label: "30 дней", days: 30 },
  { label: "За всё время" },
];

const num = new Intl.NumberFormat("ru");
const when = (iso: string) =>
  new Date(iso).toLocaleString("ru", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });

function Tile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "ok" | "bad";
}) {
  return (
    <motion.div
      className="tile"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="tile-label">{label}</div>
      <div className={`tile-value ${tone ?? ""}`}>{value}</div>
      {hint && <div className="tile-hint">{hint}</div>}
    </motion.div>
  );
}

/**
 * Панель разработчика: вся накопленная телеметрия в одном месте.
 * Данные копятся вечно и с любых устройств — здесь только чтение.
 */
export function Stats() {
  const [days, setDays] = useState<number | undefined>(undefined);
  const [overview, setOverview] = useState<MetricsOverview | null>(null);
  const [series, setSeries] = useState<DayPoint[]>([]);
  const [findings, setFindings] = useState<FindingStat[]>([]);
  const [skills, setSkills] = useState<SkillStat[]>([]);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [mix, setMix] = useState<RequestMix | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [error, setError] = useState("");
  // 401/503 — это не поломка, а «нужен токен»: показываем форму, а не ошибку.
  const [needToken, setNeedToken] = useState<"ask" | "off" | null>(null);
  const [token, setToken] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [o, t, f, s, l, m] = await Promise.all([
        metrics.overview(days),
        metrics.timeseries(days ?? 90),
        metrics.findings(),
        metrics.skills(),
        metrics.sessions(50),
        metrics.requests(),
      ]);
      setOverview(o);
      setSeries(t.days);
      setFindings(f.codes);
      setSkills(s.skills);
      setSessions(l.sessions);
      setMix(m);
      setError("");
      setNeedToken(null);
    } catch (exc) {
      const status = exc instanceof ApiError ? exc.status : 0;
      if (status === 401) setNeedToken("ask");
      else if (status === 503) setNeedToken("off");
      else setError((exc as Error).message);
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !overview) return <div className="viz-empty">Загружаю статистику…</div>;

  if (needToken === "off")
    return (
      <div className="token-gate">
        <h1 className="page-title">Статистика закрыта</h1>
        <p className="page-lede">
          В телеметрии видны тексты запросов методистов и стоимость генераций,
          поэтому раздел не открывается без токена. Задайте <code>METRICS_TOKEN</code>{" "}
          в окружении сервиса и перезапустите его.
        </p>
      </div>
    );

  if (needToken === "ask")
    return (
      <div className="token-gate">
        <h1 className="page-title">Нужен токен</h1>
        <p className="page-lede">
          Введите токен из <code>METRICS_TOKEN</code>. Он сохранится в этом браузере,
          спрашивать второй раз не будем.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            metricsToken.set(token);
            setLoading(true);
            void load();
          }}
        >
          <input
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="токен доступа"
            autoFocus
          />
          <button className="accent" type="submit">
            Открыть
          </button>
        </form>
      </div>
    );

  if (error) return <div className="ref-error">Телеметрия недоступна: {error}</div>;
  if (!overview) return null;

  const { llm, quality, phases } = overview;
  const tokens = llm.prompt_tokens + llm.completion_tokens;

  return (
    <>
      <div className="stats-head">
        <div>
          <h1 className="page-title">Статистика и отладка</h1>
          <p className="page-lede">
            Всё, что агент сделал за свою жизнь: воронка, время сборки, расход токенов,
            находки автопроверки и журнал сессий. Данные копятся без удаления, в том
            числе с разных устройств.
          </p>
        </div>
      </div>

      <div className="row spread stats-toolbar">
        <div className="seg">
          {WINDOWS.map((w) => (
            <button
              key={w.label}
              className={days === w.days ? "on" : ""}
              onClick={() => setDays(w.days)}
            >
              {w.label}
            </button>
          ))}
        </div>
        <div className="row">
          <span className="hint">это устройство · {DEVICE_ID}</span>
          <button className="btn ghost" onClick={() => void load()}>
            Обновить
          </button>
        </div>
      </div>

      <div className="tiles">
        <Tile
          label="Сессий всего"
          value={num.format(overview.sessions)}
          hint={
            overview.first_seen
              ? `первая ${when(overview.first_seen)}`
              : "ещё ни одной"
          }
        />
        <Tile label="Устройств" value={num.format(overview.devices)} hint="уникальных браузеров" />
        <Tile
          label="Доходят до комплекта"
          value={`${overview.conversion_to_ready}%`}
          tone={overview.conversion_to_ready >= 60 ? "ok" : "bad"}
          hint="от созданных сессий"
        />
        <Tile
          label="Чистая автопроверка"
          value={`${quality.clean_share}%`}
          tone={quality.clean_share >= 90 ? "ok" : "bad"}
          hint={`${quality.clean_sessions} из ${quality.checked_sessions} комплектов`}
        />
        <Tile
          label="Каркас, медиана"
          value={formatDuration(phases.skeleton?.p50 ?? 0)}
          hint={`p90 ${formatDuration(phases.skeleton?.p90 ?? 0)}`}
        />
        <Tile
          label="Комплект, медиана"
          value={formatDuration(phases.content?.p50 ?? 0)}
          hint={`p90 ${formatDuration(phases.content?.p90 ?? 0)}`}
        />
        <Tile
          label="Токенов"
          value={num.format(tokens)}
          hint={`${llm.cost_rub} ₽ · ${num.format(llm.calls)} вызовов`}
        />
        <Tile
          label="Отказы модели"
          value={num.format(llm.failed)}
          tone={llm.failed ? "bad" : "ok"}
          hint={`${llm.json_retries} повторов JSON · ${llm.avg_latency_ms} мс средняя`}
        />
      </div>

      <div className="card">
        <h3>Воронка</h3>
        <p className="card-lede">
          Сколько сессий дошло до каждого шага. Провал между «Каркас собран» и
          «Каркас утверждён» означает, что методиста не устраивает результат.
        </p>
        <FunnelBars items={overview.funnel} labels={STAGE_LABEL} />
      </div>

      <div className="card">
        <h3>По дням</h3>
        <p className="card-lede">Запуски и доведённые до конца комплекты.</p>
        <DaysChart days={series} />
      </div>

      <div className="two-col">
        <div className="card">
          <h3>Время фаз</h3>
          <table className="tight">
            <thead>
              <tr>
                <th>Фаза</th>
                <th className="num">Запусков</th>
                <th className="num">Сбоев</th>
                <th className="num">p50</th>
                <th className="num">p90</th>
                <th className="num">макс</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(phases).map(([name, p]) => (
                <tr key={name}>
                  <td>{PHASE_LABEL[name] ?? name}</td>
                  <td className="num">{p.runs}</td>
                  <td className="num" style={p.failed ? { color: "var(--red)" } : undefined}>
                    {p.failed}
                  </td>
                  <td className="num">{formatDuration(p.p50)}</td>
                  <td className="num">{formatDuration(p.p90)}</td>
                  <td className="num">{formatDuration(p.max)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h3>Выгрузки</h3>
          <p className="card-lede">Какие форматы реально забирают.</p>
          <MiniBars
            items={overview.exports.map((e) => ({
              label: e.kind,
              value: e.n,
              hint: `${Math.round(e.bytes / 1024)} КБ`,
            }))}
            color={SERIES[1]}
          />
        </div>
      </div>

      <div className="card">
        <h3>Навыки агента</h3>
        <p className="card-lede">
          Где модель тратит время и токены. Повторы JSON — признак того, что промпт
          навыка надо ужесточить.
        </p>
        <table className="tight">
          <thead>
            <tr>
              <th>Навык</th>
              <th className="num">Вызовов</th>
              <th className="num">Средняя</th>
              <th className="num">Макс</th>
              <th className="num">Промпт</th>
              <th className="num">Ответ</th>
              <th className="num">Отказов</th>
              <th className="num">Повторы JSON</th>
            </tr>
          </thead>
          <tbody>
            {skills.map((s) => (
              <tr key={s.skill}>
                <td>{s.skill}</td>
                <td className="num">{s.calls}</td>
                <td className="num">{s.avg_latency_ms} мс</td>
                <td className="num">{s.max_latency_ms} мс</td>
                <td className="num">{num.format(s.prompt_tokens)}</td>
                <td className="num">{num.format(s.completion_tokens)}</td>
                <td className="num" style={s.failed ? { color: "var(--red)" } : undefined}>
                  {s.failed}
                </td>
                <td className="num" style={s.json_retries ? { color: "var(--amber)" } : undefined}>
                  {s.json_retries}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="two-col">
        <div className="card">
          <h3>Находки автопроверки</h3>
          <p className="card-lede">Очередь задач на доработку, отсортированная по частоте.</p>
          <table className="tight">
            <thead>
              <tr>
                <th>Код</th>
                <th className="num">Раз</th>
                <th className="num">Сессий</th>
                <th>Пример</th>
              </tr>
            </thead>
            <tbody>
              {findings.map((f) => (
                <tr key={`${f.code}-${f.severity}`}>
                  <td>
                    <span className={`tag ${f.severity === "error" ? "err" : ""}`}>{f.code}</span>
                  </td>
                  <td className="num">{f.n}</td>
                  <td className="num">{f.sessions}</td>
                  <td className="muted-cell">{f.example}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h3>Что просят</h3>
          <p className="card-lede">Типы программ, объёмы и ручные правки методиста.</p>
          <MiniBars
            items={(mix?.program_types ?? []).map((t) => ({
              label: t.program_type,
              value: t.n,
            }))}
          />
          <div className="card-split" />
          <MiniBars
            items={(mix?.hours_buckets ?? []).map((b) => ({ label: `${b.bucket} ч`, value: b.n }))}
            color={SERIES[2]}
          />
          <div className="card-split" />
          <MiniBars
            items={(mix?.actions ?? []).map((a) => ({ label: a.kind, value: a.n }))}
            color={SERIES[3]}
          />
        </div>
      </div>

      <div className="card">
        <h3>Журнал сессий</h3>
        <p className="card-lede">
          Последние 50. Нажмите на строку — развернётся вся трасса: стадии, вызовы
          модели, находки и выгрузки.
        </p>
        <table className="tight">
          <thead>
            <tr>
              <th>Когда</th>
              <th>Запрос</th>
              <th>Тип</th>
              <th className="num">Часов</th>
              <th>Финал</th>
              <th className="num">Вызовов</th>
              <th className="num">Время</th>
              <th className="num">₽</th>
              <th className="num">Ошибок</th>
              <th className="num">Файлов</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr
                key={s.id}
                className="clickable"
                onClick={() =>
                  void metrics
                    .session(s.id)
                    .then(setDetail)
                    .catch((e) => setError((e as Error).message))
                }
              >
                <td>{when(s.created_at)}</td>
                <td className="muted-cell">{s.request_text.slice(0, 70)}</td>
                <td>{s.program_type || "—"}</td>
                <td className="num">{s.total_hours ?? "—"}</td>
                <td>
                  <span
                    className={`tag ${
                      s.last_stage === "ready" ? "green" : s.last_stage === "failed" ? "err" : ""
                    }`}
                  >
                    {STAGE_LABEL[s.last_stage ?? ""] ?? s.last_stage ?? "—"}
                  </span>
                </td>
                <td className="num">{s.calls}</td>
                <td className="num">{s.seconds ? formatDuration(s.seconds) : "—"}</td>
                <td className="num">{s.cost_rub ?? "—"}</td>
                <td className="num" style={s.errors ? { color: "var(--red)" } : undefined}>
                  {s.errors}
                </td>
                <td className="num">{s.exports}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <AnimatePresence>
        {detail && (
          <motion.div
            className="drawer-back"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setDetail(null)}
          >
            <motion.div
              className="drawer"
              initial={{ x: 40, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={{ x: 40, opacity: 0 }}
              transition={{ type: "spring", stiffness: 300, damping: 30 }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="row spread">
                <h3>Сессия {String(detail.session.id ?? "")}</h3>
                <button className="tiny ghost" onClick={() => setDetail(null)}>
                  ✕
                </button>
              </div>
              <p className="card-lede">{String(detail.session.request_text ?? "")}</p>

              <h4>Стадии</h4>
              {detail.stages.map((s, i) => (
                <div className="trace-row" key={i}>
                  <span className="mono">{when(s.ts)}</span>
                  <b>{STAGE_LABEL[s.stage] ?? s.stage}</b>
                  <span className="muted-cell">{s.note}</span>
                </div>
              ))}

              <h4>Вызовы модели</h4>
              {detail.calls.map((c, i) => (
                <div className="trace-row" key={i}>
                  <span className="mono">{c.skill}</span>
                  <span className="mono">{c.latency_ms} мс</span>
                  <span className="muted-cell">
                    {c.prompt_tokens}+{c.completion_tokens} ток.
                    {!c.ok && <b style={{ color: "var(--red)" }}> · {c.error}</b>}
                  </span>
                </div>
              ))}

              {detail.findings.length > 0 && (
                <>
                  <h4>Находки</h4>
                  {detail.findings.map((f, i) => (
                    <div className="trace-row" key={i}>
                      <span className={`tag ${f.severity === "error" ? "err" : ""}`}>{f.code}</span>
                      <span className="muted-cell">{f.message}</span>
                    </div>
                  ))}
                </>
              )}

              {detail.actions.length > 0 && (
                <>
                  <h4>Действия методиста</h4>
                  {detail.actions.map((a, i) => (
                    <div className="trace-row" key={i}>
                      <span className="mono">{a.kind}</span>
                      <span className="muted-cell">{a.payload}</span>
                    </div>
                  ))}
                </>
              )}

              {detail.exports.length > 0 && (
                <>
                  <h4>Выгрузки</h4>
                  {detail.exports.map((e, i) => (
                    <div className="trace-row" key={i}>
                      <span className="mono">{e.kind}</span>
                      <span className="muted-cell">{Math.round(e.bytes / 1024)} КБ</span>
                    </div>
                  ))}
                </>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
