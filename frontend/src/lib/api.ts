import type { SessionState } from "./types";

const BASE = "/api";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail.slice(0, 300)}`);
  }
  return (await response.json()) as T;
}

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
  const response = await fetch(`${BASE}${path}`, { method: "POST", body: form });
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
  regenerateTopic: (id: string, topicId: string) =>
    call<SessionState>(`/sessions/${id}/topics/${topicId}/regenerate`, {
      method: "POST",
      body: JSON.stringify({ regenerate_tests: true }),
    }),
  state: (id: string) => call<SessionState>(`/sessions/${id}`),
  exportUrl: (id: string, kind: string) => `${BASE}/sessions/${id}/export/${kind}`,
};
