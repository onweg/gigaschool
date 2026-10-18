import type { SessionState } from "./types";

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

/** Ошибка с кодом ответа: интерфейсу нужно отличить «нужен токен» от прочего. */
export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const token = path.startsWith("/metrics") ? metricsToken.get() : "";
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Device-Id": DEVICE_ID,
      ...(token ? { "X-Metrics-Token": token } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new ApiError(response.status, `${response.status}: ${detail.slice(0, 300)}`);
  }
  return (await response.json()) as T;
}

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
  const response = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "X-Device-Id": DEVICE_ID },
    body: form,
  });
  if (!response.ok) {
    let detail = await response.text();
    try {
      detail = JSON.parse(detail).detail ?? detail;
    } catch {
      /* ответ не JSON — показываем как есть */
    }
    throw new Error(detail.slice(0, 200));
  }
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
  parseReference: (file: File) => upload<ParsedReference>("/references/parse", file),
  health: () => call<{ status: string; llm_provider: string; llm_model: string }>("/health"),
  skills: () => call<{ name: string; title: string; description: string }[]>("/skills"),
  createSession: (text: string, references: string[] = []) =>
    call<SessionState>("/sessions", {
      method: "POST",
      body: JSON.stringify({ text, references }),
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
