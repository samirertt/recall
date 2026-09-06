import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import CaptureForm from "../components/CaptureForm";
import ResultCard from "../components/ResultCard";
import SearchBar from "../components/SearchBar";
import { api } from "../lib/api";

export default function HomePage() {
  const [query, setQuery] = useState("");

  const searchResults = useQuery({
    queryKey: ["search", query],
    queryFn: () => api.search(query),
    enabled: query.trim().length > 0,
  });

  const recentIncidents = useQuery({
    queryKey: ["incidents"],
    queryFn: () => api.listIncidents(),
    enabled: query.trim().length === 0,
  });

  return (
    <div className="flex flex-col gap-6">
      <SearchBar value={query} onChange={setQuery} />

      {query.trim() ? (
        <section>
          {searchResults.isLoading && (
            <p className="text-sm text-neutral-400">Searching…</p>
          )}
          {searchResults.data?.degraded.vector_search && (
            <p className="mb-2 text-xs text-amber-600 dark:text-amber-400">
              Semantic search not yet available — showing lexical results only.
            </p>
          )}
          {searchResults.data?.results.length === 0 && (
            <p className="text-sm text-neutral-400">No results for "{query}".</p>
          )}
          <div className="flex flex-col gap-2">
            {searchResults.data?.results.map((r) => (
              <ResultCard key={r.incident_id} result={r} />
            ))}
          </div>
        </section>
      ) : (
        <>
          <CaptureForm />
          <section>
            <h2 className="mb-2 text-sm font-semibold text-neutral-500">Recent Incidents</h2>
            {recentIncidents.isLoading && (
              <p className="text-sm text-neutral-400">Loading…</p>
            )}
            {recentIncidents.data?.length === 0 && (
              <p className="text-sm text-neutral-400">
                Nothing captured yet — try the form above.
              </p>
            )}
            <div className="flex flex-col gap-2">
              {recentIncidents.data?.map((incident) => (
                <Link
                  key={incident.id}
                  to={`/incidents/${incident.id}`}
                  className="block rounded-lg border border-neutral-200 p-3 text-sm hover:border-neutral-400 dark:border-neutral-800 dark:hover:border-neutral-600"
                >
                  <span className="font-medium">
                    {incident.title ?? `Incident #${incident.id}`}
                  </span>
                  <span className="ml-2 text-xs text-neutral-400">{incident.status}</span>
                  {incident.needs_ai_review && (
                    <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                      needs review
                    </span>
                  )}
                </Link>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
