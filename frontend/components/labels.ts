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


export const TOOL_NAMES: Record<string, string> = {
  search_datasets: "Dataset search", search_documents: "Document search", web_search: "Web search",
  compute_correlation: "Correlation", list_records: "Record lists", create_chart: "Charts",
  load_dataset: "loading data", describe_dataset: "dataset values", compute_trend: "trends",
  compare_groups: "comparisons", rank_changes: "rankings",
};

/** The Detail levels as the UI names them (the API's level 1, 2 and 3). */
export const DETAIL_LABELS: Record<number, string> = { 1: "Normal", 2: "Thinking", 3: "Verbose" };

export const EXAMPLES = [
  "How has employment in the information and communications sector changed since 2019, and how does it compare with overall employment?",
  "Which HDB towns saw the largest rise in median resale prices since 2020?",
  "How have COE premiums for Category A cars moved over the last five years?",
  "How has life expectancy at birth changed in Singapore over the past decade, for men and women?",
];
