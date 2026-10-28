export type MaterialValue = Record<string, string | string[] | number[] | { title: string; bullets: string[]; notes: string }[]>;
export type ReviewItem = {
  id: string; kind: string; label: string; title: string; topic_id: string; topic_label: string;
  revision: string; value: MaterialValue; status: 'todo' | 'reviewed' | 'deferred' | 'needs_changes' | 'stale';
  decision: { author: string; comment: string; at: string } | null;
  findings: { id: string; title: string; reason: string; quote: string; suggestion: string; dismissed: boolean }[];
};
export type Proposal = {
  id: string; item_id: string; title: string; revision: string; before: MaterialValue; after: MaterialValue;
  author: string; comment: string; at: string; status: 'pending' | 'accepted' | 'rejected'; stale: boolean;
};
export type Approval = { decision: 'approved' | 'returned'; author: string; comment: string; at: string; stale?: boolean };
export type ReviewData = {
  revision: string; items: ReviewItem[]; proposals: Proposal[]; approval: Approval | null; approvals: Approval[];
  scans: Record<string, { revision: string; at: string; stale: boolean; offline: boolean }>;
  summary: { total: number; reviewed: number; pending: number; needs_changes: number; stale: number };
};
