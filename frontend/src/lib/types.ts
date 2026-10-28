export type Hours = {
  lectures: number;
  practice: number;
  labs: number;
  self_study: number;
  total: number;
  contact: number;
};

export type Topic = {
  id: string;
  code: string;
  title: string;
  hours: Hours;
  content: string;
  activities: string;
  outcome_ids: string[];
};

export type Module = {
  id: string;
  index: number;
  title: string;
  hours: Hours;
  assessment_form: string;
  topics: Topic[];
};

export type Outcome = { id: string; kind: string; text: string };

export type Program = {
  learning_preferences?: Record<string, string | string[]>;
  meta: {
    title: string;
    program_type: "dpp_pk" | "dop_obsh";
    total_hours: number;
    audience: string;
    study_form: string;
    issued_document: string;
  };
  goal: string;
  objectives: string[];
  outcomes: Outcome[];
  competencies: { id: string; title: string; prof_standard_code?: string; labor_function?: string }[];
  prof_standards: { code: string; title: string; verification: string; note: string }[];
  legal_acts: { title: string; requisites: string; verification: string }[];
  modules: Module[];
  final_attestation: { hours: number; form: string; description: string; criteria: string[] };
  test_questions: { id: string; stem: string; options: string[]; correct: number[]; outcome_ids: string[]; topic_id: string }[];
  seminar_tasks: { id: string; title: string; statement: string; criteria: string[]; outcome_ids: string[] }[];
  lecture_plans: { id: string; title: string; slides: { title: string; bullets: string[] }[] }[];
  sources: { citation: string; url?: string; verification: string }[];
};

export type Finding = {
  code: string;
  severity: "error" | "warning" | "info";
  message: string;
  path: string;
  hint: string;
};

export type SessionState = {
  id: string;
  model_id?: string;
  /** true — ответ собран офлайн-заглушкой, а не моделью. */
  offline_stub?: boolean;
  stage: "created" | "clarifying" | "skeleton" | "approved" | "content" | "ready" | "failed";
  request: Record<string, unknown>;
  missing: string[];
  questions: { field: string; question: string; answer?: string }[];
  program: Program | null;
  report: { findings: Finding[] } | null;
  report_summary: { ok: boolean; errors: number; warnings: number; checks: number } | null;
  events: { at: string; stage: string; message: string; level: string }[];
  timings: Record<string, number>;
  phase: string;
  phase_started_at: string | null;
  economics: {
    tokens: number;
    llm_calls: number;
    cost_rub: number;
    manual_hours_baseline: number;
    savings_rub: number;
  };
  error: string;
  /** Поля, ответ на которые не удалось распознать, и текст переспроса. */
  unparsed: string[];
  reask: Record<string, string>;
};
