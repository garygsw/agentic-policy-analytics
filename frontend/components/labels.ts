// Human names for graph nodes and run outcomes.

const NODES: Record<string, string> = {
  detect_language: "Language", understand: "Question", triage: "Triage", clarify: "Clarify", ask_user: "Clarify",
  redirect: "Scope", coordinator: "Coordinator", approve_plan: "Plan", decline: "Coordinator",
  extraction: "Extraction", analytics: "Analytics", report_writer: "Report writer", validate: "Number check",
  reviewer: "Reviewer", localize: "Translation", localize_report: "Translation", render: "Report",
  fallback_report: "Fallback", cancel: "Run", respond: "Run", run: "Run", user: "You",
};

export function nodeLabel(node: string): string {
  return NODES[node] ?? node;
}

export const OUTCOMES: Record<string, { label: string; tone: "ok" | "warn" | "muted" }> = {
  answered: { label: "Answered", tone: "ok" },
  partial: { label: "Partial answer", tone: "warn" },
  off_topic: { label: "Outside scope", tone: "muted" },
  declined: { label: "Not answerable with the available data", tone: "muted" },
  fallback: { label: "Automated summary", tone: "warn" },
  cancelled: { label: "Cancelled", tone: "muted" },
  failed: { label: "Failed", tone: "warn" },
};

export const TRIAGE_LABELS: Record<string, string> = {
  local: "Local classifier (LLM when unsure)",
  jev: "Jev (TypeSafe)",
  llm: "Fast LLM",
};

export const SCOPE_LABELS: Record<string, string> = { gov: "Government sites", any: "Whole web" };

export const EXAMPLES = [
  "How has employment in the information and communications sector changed since 2019, and how does it compare with overall employment?",
  "Which HDB towns saw the largest rise in median resale prices since 2020?",
  "How have COE premiums for Category A cars moved over the last five years?",
  "How has life expectancy at birth changed in Singapore over the past decade, for men and women?",
];
