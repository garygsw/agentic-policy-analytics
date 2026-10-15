// A thin client for the FastAPI backend. Every call returns parsed JSON or throws ApiError.
import type {
  OptionsResponse, RunDetail, RunOptions, ThreadDetail, ThreadSummary, TraceEvent,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown, message: string) {
    super(message);
  }
}

/** The message to show for a failed request: the API's own message when it has one. */
export function errorMessage(status: number, detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) return String(detail.message);
  if (status === 0) return "Cannot reach the API. Is it running?";
  return `Request failed (HTTP ${status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(API_URL + path, { ...init, headers: { "Content-Type": "application/json" } });
  } catch {
    throw new ApiError(0, null, errorMessage(0, null));
  }
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = (await response.json()).detail;
    } catch {
      // not JSON: keep the status only
    }
    throw new ApiError(response.status, detail, errorMessage(response.status, detail));
  }
  return response.json() as Promise<T>;
}

const post = (body?: unknown): RequestInit => ({ method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  options: () => request<OptionsResponse>("/api/options"),
  threads: () => request<ThreadSummary[]>("/api/threads"),
  thread: (id: string) => request<ThreadDetail>(`/api/threads/${id}`),
  createThread: () => request<{ id: string }>("/api/threads", post()),
  send: (threadId: string, text: string, options: RunOptions, requirePlanApproval: boolean) =>
    request<{ run_id: string }>(`/api/threads/${threadId}/messages`,
      post({ text, options, require_plan_approval: requirePlanApproval })),
  answer: (runId: string, answer: string, options: RunOptions) =>
    request<{ run_id: string }>(`/api/runs/${runId}/resume`, post({ answer, options })),
  decidePlan: (runId: string, approved: boolean, feedback: string, options: RunOptions) =>
    request<{ run_id: string }>(`/api/runs/${runId}/resume`, post({ approved, feedback, options })),
  cancel: (runId: string) => request<{ status: string }>(`/api/runs/${runId}/cancel`, post()),
  run: (runId: string) => request<RunDetail>(`/api/runs/${runId}`),
  events: (runId: string, after = 0) => request<TraceEvent[]>(`/api/runs/${runId}/events?after=${after}`),
};

export const reportUrl = (runId: string) => `${API_URL}/api/runs/${runId}/report.html`;
export const markdownUrl = (runId: string) => `${API_URL}/api/runs/${runId}/report.md`;
export const pdfUrl = (runId: string) => `${API_URL}/api/runs/${runId}/report.pdf`;
export const streamUrl = (runId: string, after: number) =>
  `${API_URL.replace(/^http/, "ws")}/api/runs/${runId}/stream?after=${after}`;
