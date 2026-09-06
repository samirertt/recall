import { Link } from "react-router-dom";
import type { SearchResult } from "../lib/api";

const STATUS_COLORS: Record<string, string> = {
  unresolved: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  investigating: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  solved: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  abandoned: "bg-neutral-200 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400",
  obsolete: "bg-neutral-200 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-500",
};

function escapeHtml(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function highlightSnippet(snippet: string): string {
  // Escape first, then reintroduce **bold** markers from our own FTS5 snippet() output —
  // never trust incident text (user-entered) to be safe to inject as raw HTML.
  return escapeHtml(snippet).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function signalBadges(signals: SearchResult["signals"]): string[] {
  const badges: string[] = [];
  if (signals.lexical) badges.push("lexical");
  if (signals.trigram) badges.push("substring");
  if (signals.attachment) badges.push("attachment");
  if (signals.vector) badges.push("semantic");
  if (signals.relationship) badges.push("related");
  return badges;
}

export default function ResultCard({ result }: { result: SearchResult }) {
  return (
    <Link
      to={`/incidents/${result.incident_id}`}
      className="block rounded-lg border border-neutral-200 p-4 transition hover:border-neutral-400 dark:border-neutral-800 dark:hover:border-neutral-600"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-medium">{result.title ?? `Incident #${result.incident_id}`}</h3>
        <span
          className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[result.status] ?? ""}`}
        >
          {result.status}
        </span>
      </div>
      {result.snippet && (
        <p
          className="mt-2 text-sm text-neutral-500 [&_strong]:text-neutral-900 dark:text-neutral-400 dark:[&_strong]:text-neutral-100"
          dangerouslySetInnerHTML={{ __html: highlightSnippet(result.snippet) }}
        />
      )}
      <div className="mt-2 flex items-center gap-2 text-xs text-neutral-400">
        <span>relevance {(result.fused_score * 100).toFixed(0)}%</span>
        {signalBadges(result.signals).map((badge) => (
          <span
            key={badge}
            className="rounded bg-neutral-100 px-1.5 py-0.5 dark:bg-neutral-800"
          >
            {badge}
          </span>
        ))}
      </div>
    </Link>
  );
}
