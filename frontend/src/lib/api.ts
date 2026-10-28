import type { SessionState } from "./types";
import type { MaterialValue, ReviewData } from "./review";

const BASE = "/api";

/**
 * Идентификатор устройства. Генерируется один раз и живёт в localStorage,
 * поэтому статистика копится отдельно по каждому браузеру, а на сервере
 * никогда не стирается. Персональных данных не содержит — случайный id.
 */
export const DEVICE_ID = (() => {
  const KEY = "acr.device";
  try {
    const saved = localStorage.getItem(KEY);
    if (saved) return saved;
    const fresh =
      (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/-/g, "").slice(0, 16);
    localStorage.setItem(KEY, fresh);
    return fresh;
  } catch {
    return "no-storage";
  }
})();

/** Токен к телеметрии. Хранится у того, кто его ввёл, и больше нигде:
 *  в статистике видны тексты запросов методистов и стоимость генераций. */
const TOKEN_KEY = "acr.metrics-token";

export const metricsToken = {
  get(): string {
    try {
      // Удобный вход по ссылке вида #/stats?token=… — один раз и в хранилище.
      const fromUrl = new URLSearchParams(location.hash.split("?")[1] ?? "").get("token");
      if (fromUrl) {
        localStorage.setItem(TOKEN_KEY, fromUrl);
        location.hash = location.hash.split("?")[0];
        return fromUrl;
      }
      return localStorage.getItem(TOKEN_KEY) ?? "";
    } catch {
      return "";
    }
  },
  set(value: string) {
    try {
      localStorage.setItem(TOKEN_KEY, value.trim());
    } catch {
      /* приватный режим — просто не запомним */
    }
  },
  clear() {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* нечего чистить */
    }
  },
};

/** Ошибка, понятная человеку: что случилось и что с этим делать.
 *  `status` нужен интерфейсу, чтобы отличить «нужен токен» от прочего;
 *  0 — до сервера не достучались вовсе. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly hint = ""
  ) {
    super(message);
  }

  /** Одной строкой для чата: причина и, если есть, что сделать. */
  get full(): string {
    return this.hint ? `${this.message}. ${this.hint}` : this.message;
  }
}

/** Что сказать человеку по коду ответа, когда сервер сам ничего не объяснил. */
const STATUS_TEXT: Record<number, [string, string]> = {
  400: ["Запрос не принят", "Проверьте введённые данные"],
  404: ["Не найдено", "Сессия могла устареть — начните новую программу"],
  409: ["Сейчас этот шаг недоступен", "Дождитесь окончания предыдущего"],
  413: ["Файл слишком большой", "Приложите программу поменьше или текстом"],
  429: ["Модель перегружена", "Подождите минуту и повторите"],
  500: ["Внутренняя ошибка агента", "Повторите; если не проходит — смотрите журнал сервера"],
  502: ["Агент не отвечает", "Сервис перезапускается или упал — смотрите журнал сервера"],
  503: ["Агент временно недоступен", "Повторите через минуту"],
  504: ["Агент не успел ответить", "Генерация идёт дольше обычного — повторите"],
};

/** Разобрать неудачный ответ в человеческое объяснение.
 *
 *  Раньше в чат уходило «502: <html><head><title>502 Bad Gateway…» целиком:
 *  nginx отвечает страницей, а не JSON. Теперь — причина и что делать. */
async function explain(response: Response): Promise<ApiError> {
  const status = response.status;
  const [fallback, hint] = STATUS_TEXT[status] ?? [`Ошибка ${status}`, ""];
  const body = await response.text().catch(() => "");

  let detail: unknown = null;
  try {
    detail = JSON.parse(body).detail;
  } catch {
    /* не JSON — скорее всего страница ошибки прокси */
  }

  if (typeof detail === "string" && detail.trim()) {
    // Сервер сам объяснил причину — значит он жив, и «сервис упал» было бы
    // неправдой. Остаётся одно: модель или сеть до неё подвели — повторить.
    return new ApiError(status, detail.trim(), status >= 500 ? "Повторите через минуту" : "");
  }
  if (detail && typeof detail === "object") {
    // {error: "...", errors: [...]} — например, каркас не проходит автопроверку
    const obj = detail as { error?: string; missing?: string[]; errors?: { message: string }[] };
    const parts = [obj.error ?? fallback];
    let advice = hint;
    if (obj.missing?.length) {
      parts.push(`не хватает: ${obj.missing.join(", ")}`);
      advice = "Ответьте на уточняющие вопросы";
    }
    if (obj.errors?.length) {
      parts.push(obj.errors.map((e) => e.message).slice(0, 3).join("; "));
      advice = "Исправьте замечания автопроверки и повторите";
    }
    return new ApiError(status, parts.join(" — "), advice);
  }
  return new ApiError(status, fallback, hint);
}

/** Обрыв сети выглядит в браузере как «Failed to fetch» — переводим. */
function offline(): ApiError {
  return new ApiError(
    0,
    "Нет связи с сервером агента",
    "Проверьте интернет; если он есть — сервис не запущен или недоступен"
  );
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const token = path.startsWith("/metrics") ? metricsToken.get() : "";
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Device-Id": DEVICE_ID,
        ...(token ? { "X-Metrics-Token": token } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw offline();
  }
  if (!response.ok) throw await explain(response);
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, "Сервер ответил не в том формате", "Повторите запрос");
  }
}

export type PendingSource = {
  kind: "prof_standard" | "legal_act" | "source";
  key: string;
  title: string;
  what_to_check: string;
  check_url: string;
};

export type ModelCatalog = {
  models: { id: string; name: string; description: string; reasoning: boolean; input_per_million: number | null; output_per_million: number | null }[];
  default_id: string;
  offline_stub: boolean;
};

export type Health = {
  status: string;
  llm_provider: string;
  llm_model: string;
  /** true — сервис на офлайн-заглушке: содержание программ не настоящее. */
  offline_stub?: boolean;
  note?: string;
};

export type AdaptChanges = {
  audience?: string;
  total_hours?: number;
  program_type?: "dpp_pk" | "dop_obsh";
  entry_level?: string;
};

export type RefreshFinding = {
  topic_code: string;
  what: string;
  why: string;
  replacement: string;
  severity: "outdated" | "aging" | "missing";
  severity_word: string;
};

export type RefreshResult = {
  verdict: string;
  findings: RefreshFinding[];
  summary: { outdated: number; missing: number; total: number };
  disclaimer: string;
};

export type ParsedReference = {
  filename: string;
  size_bytes: number;
  chars: number;
  preview: string;
  text: string;
};

async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  // Content-Type не задаём: граница multipart проставляется браузером.
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "X-Device-Id": DEVICE_ID },
      body: form,
    });
  } catch {
    throw offline();
  }
  if (!response.ok) throw await explain(response);
  return (await response.json()) as T;
}

export const metrics = {
  overview: (days?: number) =>
    call<MetricsOverview>(`/metrics/overview${days ? `?days=${days}` : ""}`),
  timeseries: (days = 30) => call<{ days: DayPoint[] }>(`/metrics/timeseries?days=${days}`),
  findings: () => call<{ codes: FindingStat[] }>("/metrics/findings"),
  skills: () => call<{ skills: SkillStat[] }>("/metrics/skills"),
  sessions: (limit = 50) => call<{ sessions: SessionRow[] }>(`/metrics/sessions?limit=${limit}`),
  requests: () => call<RequestMix>("/metrics/requests"),
  session: (id: string) => call<SessionDetail>(`/metrics/session/${id}`),
};

export const api = {
  models: () => call<ModelCatalog>("/models"),
  setModel: (id: string, model_id: string) => call<SessionState>(`/sessions/${id}/model`, { method: "POST", body: JSON.stringify({ model_id }) }),
  review: (id: string) => call<ReviewData>(`/sessions/${id}/review`),
  scanReview: (id: string, topic_id: string) => call<ReviewData>(`/sessions/${id}/review/scan`, {
    method: "POST", body: JSON.stringify({ topic_id }),
  }),
  reviewBatch: (id: string, items: { item_id: string; revision: string }[], author: string) =>
    call<ReviewData>(`/sessions/${id}/review/decisions/batch`, { method: "POST", body: JSON.stringify({ items, author }) }),
  approvalRecordUrl: (id: string) => `${BASE}/sessions/${id}/review/approval-record.docx`,
  reviewDecision: (id: string, data: { item_id: string; revision: string; status: string; author: string; comment: string }) =>
    call<ReviewData>(`/sessions/${id}/review/decisions`, { method: "POST", body: JSON.stringify(data) }),
  dismissFinding: (id: string, finding: string, revision: string) =>
    call<ReviewData>(`/sessions/${id}/review/findings/${finding}/dismiss`, { method: "POST", body: JSON.stringify({ revision }) }),
  askReview: (id: string, data: { item_id: string; revision: string; instruction: string }) =>
    call<{ answer: string }>(`/sessions/${id}/review/ask`, { method: "POST", body: JSON.stringify(data) }),
  suggestEdit: (id: string, data: { item_id: string; revision: string; instruction: string }) =>
    call<{ replacement: MaterialValue; offline: boolean }>(`/sessions/${id}/review/suggest`, { method: "POST", body: JSON.stringify(data) }),
  proposeEdit: (id: string, data: { item_id: string; revision: string; after: MaterialValue; author: string; comment: string }) =>
    call<ReviewData>(`/sessions/${id}/review/proposals`, { method: "POST", body: JSON.stringify(data) }),
  resolveEdit: (id: string, proposal: string, action: "accept" | "reject") =>
    call<{ review: ReviewData; session: SessionState }>(`/sessions/${id}/review/proposals/${proposal}/resolve`, {
      method: "POST", body: JSON.stringify({ action }),
    }),
  approval: (id: string, data: { revision: string; decision: "approved" | "returned"; author: string; comment: string }) =>
    call<ReviewData>(`/sessions/${id}/review/approval`, { method: "POST", body: JSON.stringify(data) }),
  parseReference: (file: File) => upload<ParsedReference>("/references/parse", file),
  health: () => call<Health>("/health"),
  skills: () => call<{ name: string; title: string; description: string }[]>("/skills"),
  createSession: (text: string, references: string[] = [], answers: Record<string, string> = {}, model_id?: string) =>
    call<SessionState>("/sessions", {
      method: "POST",
      body: JSON.stringify({ text, references, answers, model_id }),
    }),
  answer: (id: string, answers: Record<string, string>) =>
    call<SessionState>(`/sessions/${id}/answers`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    }),
  skeleton: (id: string) => call<SessionState>(`/sessions/${id}/skeleton`, { method: "POST" }),
  approve: (id: string) => call<SessionState>(`/sessions/${id}/approve`, { method: "POST" }),
  content: (id: string, testTotal: number | null, slidesModules: number[] | null) =>
    call<SessionState>(`/sessions/${id}/content`, {
      method: "POST",
      body: JSON.stringify({ test_total: testTotal, slides_modules: slidesModules }),
    }),
  regenerateTopic: (id: string, topicId: string, instruction = "", title = "") =>
    call<SessionState>(`/sessions/${id}/topics/${topicId}/regenerate`, {
      method: "POST",
      body: JSON.stringify({ instruction, title, regenerate_tests: true }),
    }),
  /** Переработать готовую программу под новые условия: исходная не меняется,
   *  ответ — новая сессия с каркасом и списком изменений. */
  adapt: (id: string, changes: AdaptChanges) =>
    call<SessionState & { adapted_from: string; changes: string[] }>(
      `/sessions/${id}/adapt`,
      { method: "POST", body: JSON.stringify(changes) }
    ),
  /** Проверить программу на устаревшие темы и инструменты. */
  refresh: (id: string) =>
    call<RefreshResult>(`/sessions/${id}/refresh`, { method: "POST" }),
  /** Источники, которые агент не подтвердил сам, — с адресом, где проверить. */
  pendingSources: (id: string) =>
    call<{ items: PendingSource[] }>(`/sessions/${id}/sources/pending`),
  /** Решение методиста: подтвердить после проверки или убрать из программы. */
  reviewSource: (id: string, kind: string, key: string, action: "confirm" | "remove") =>
    call<SessionState & { pending_sources: PendingSource[] }>(`/sessions/${id}/sources/review`, {
      method: "POST",
      body: JSON.stringify({ kind, key, action }),
    }),
  state: (id: string) => call<SessionState>(`/sessions/${id}`),
  exportUrl: (id: string, kind: string) => `${BASE}/sessions/${id}/export/${kind}`,
};

/* ------------------------------------------------------- Типы телеметрии */
export type FunnelStep = { stage: string; sessions: number; share: number };
export type PhaseStat = {
  runs: number;
  failed: number;
  p50: number;
  p90: number;
  max: number;
};

export type MetricsOverview = {
  window_days: number | null;
  sessions: number;
  devices: number;
  first_seen: string | null;
  funnel: FunnelStep[];
  conversion_to_ready: number;
  phases: Record<string, PhaseStat>;
  llm: {
    calls: number;
    failed: number;
    json_retries: number;
    avg_latency_ms: number;
    prompt_tokens: number;
    completion_tokens: number;
    cost_rub: number;
  };
  quality: { checked_sessions: number; clean_sessions: number; clean_share: number };
  exports: { kind: string; n: number; bytes: number }[];
};

export type DayPoint = { day: string; sessions: number; ready: number; cost_rub: number };

export type FindingStat = {
  code: string;
  severity: string;
  n: number;
  sessions: number;
  example: string;
};

export type SkillStat = {
  skill: string;
  calls: number;
  avg_latency_ms: number;
  max_latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  failed: number;
  json_retries: number;
};

export type SessionRow = {
  id: string;
  created_at: string;
  device_id: string;
  program_type: string;
  total_hours: number | null;
  questions_asked: number;
  has_references: number;
  provider: string;
  model: string;
  request_text: string;
  last_stage: string | null;
  calls: number;
  seconds: number | null;
  cost_rub: number | null;
  errors: number;
  exports: number;
};

export type RequestMix = {
  program_types: { program_type: string; n: number }[];
  hours_buckets: { bucket: string; n: number }[];
  actions: { kind: string; n: number }[];
};

export type SessionDetail = {
  session: Record<string, unknown>;
  stages: { ts: string; stage: string; note: string }[];
  runs: Record<string, unknown>[];
  calls: {
    ts: string;
    skill: string;
    model: string;
    latency_ms: number;
    prompt_tokens: number;
    completion_tokens: number;
    ok: number;
    error: string;
  }[];
  findings: { ts: string; check_stage: string; code: string; severity: string; message: string; path: string }[];
  exports: { ts: string; kind: string; bytes: number }[];
  actions: { ts: string; kind: string; payload: string }[];
};
