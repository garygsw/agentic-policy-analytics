// Number formatting for counters: tokens, cost and elapsed time.

export function formatTokens(n: number): string {
  if (n < 1_000) return String(Math.round(n));
  if (n < 1_000_000) return `${(n / 1_000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

export function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

export function formatElapsed(seconds: number): string {
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (v: number) => String(v).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}

export function percent(part: number, whole: number): string {
  if (!whole) return "0%";
  const p = (part / whole) * 100;
  return p > 0 && p < 0.1 ? "<0.1%" : `${p.toFixed(p < 10 ? 1 : 0)}%`;
}
