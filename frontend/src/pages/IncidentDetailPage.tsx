import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Incident } from "../lib/api";

const FIELD_LABELS: [keyof Incident, string][] = [
  ["symptoms", "Symptoms"],
  ["root_cause", "Root Cause"],
  ["solution", "Solution"],
  ["why_solution_worked", "Why It Worked"],
  ["lesson_learned", "Lesson Learned"],
];

function Field({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-400">
        {label}
      </h3>
      <p className="mt-1 whitespace-pre-wrap text-sm">{value}</p>
    </div>
  );
}

export default function IncidentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const incidentId = Number(id);
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [file, setFile] = useState<File | null>(null);

  const incidentQuery = useQuery({
    queryKey: ["incident", incidentId],
    queryFn: () => api.getIncident(incidentId),
  });

  const attachmentsQuery = useQuery({
    queryKey: ["attachments", incidentId],
    queryFn: () => api.listAttachments(incidentId),
  });

  const updateMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => api.updateIncident(incidentId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["incident", incidentId] });
      setEditing(false);
    },
  });

  const archiveMutation = useMutation({
    mutationFn: () => api.archiveIncident(incidentId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["incident", incidentId] }),
  });

  // Section 50: Escape cancels the edit form, matching the palette's own Escape-to-close.
  useEffect(() => {
    if (!editing) return;
    function handleKeydown(e: KeyboardEvent) {
      if (e.key === "Escape") setEditing(false);
    }
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [editing]);

  const uploadMutation = useMutation({
    mutationFn: (f: File) => api.uploadAttachment(incidentId, f),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["attachments", incidentId] });
      setFile(null);
    },
  });

  const relatedQuery = useQuery({
    queryKey: ["related", incidentId],
    queryFn: () => api.getRelatedIncidents(incidentId),
  });

  const suggestedQuery = useQuery({
    queryKey: ["suggested-relations", incidentId],
    queryFn: () => api.getSuggestedRelations(incidentId),
  });

  const linkMutation = useMutation({
    mutationFn: (targetId: number) => api.createRelation(incidentId, targetId, "related_to"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["related", incidentId] });
      queryClient.invalidateQueries({ queryKey: ["suggested-relations", incidentId] });
    },
  });

  if (incidentQuery.isLoading) return <p className="text-sm text-neutral-400">Loading…</p>;
  if (incidentQuery.isError || !incidentQuery.data)
    return <p className="text-sm text-red-600">Incident not found.</p>;

  const incident = incidentQuery.data;

  function startEditing() {
    setDraft({
      root_cause: incident.root_cause ?? "",
      solution: incident.solution ?? "",
      why_solution_worked: incident.why_solution_worked ?? "",
      lesson_learned: incident.lesson_learned ?? "",
    });
    setEditing(true);
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">
            {incident.title ?? `Incident #${incident.id}`}
          </h1>
          <div className="mt-1 flex items-center gap-2 text-xs text-neutral-400">
            <span>{incident.status}</span>
            {incident.needs_ai_review && (
              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                needs review
              </span>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          {!editing && (
            <button
              onClick={startEditing}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm dark:border-neutral-700"
            >
              Edit
            </button>
          )}
          {incident.status !== "obsolete" && (
            <button
              onClick={() => archiveMutation.mutate()}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-500 dark:border-neutral-700"
            >
              Archive
            </button>
          )}
        </div>
      </div>

      <Field label="Problem" value={incident.raw_problem} />
      <Field label="Reported Solution" value={incident.raw_solution} />

      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            updateMutation.mutate(draft);
          }}
          className="flex flex-col gap-3 rounded-lg border border-neutral-200 p-4 dark:border-neutral-800"
        >
          {(["root_cause", "solution", "why_solution_worked", "lesson_learned"] as const).map(
            (field) => (
              <div key={field} className="flex flex-col gap-1">
                <label className="text-xs font-medium text-neutral-500">
                  {FIELD_LABELS.find(([f]) => f === field)?.[1] ?? field}
                </label>
                <textarea
                  value={draft[field] ?? ""}
                  onChange={(e) => setDraft({ ...draft, [field]: e.target.value })}
                  rows={2}
                  className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900"
                />
              </div>
            ),
          )}
          <div className="flex gap-2">
            <button
              type="submit"
              className="w-fit rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white dark:bg-neutral-100 dark:text-neutral-900"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="w-fit rounded-md px-3 py-1.5 text-sm text-neutral-500"
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          {FIELD_LABELS.map(([field, label]) => (
            <Field key={field} label={label} value={incident[field] as string | null} />
          ))}
        </>
      )}

      {incident.environment && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-400">
            Environment
          </h3>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {Object.entries(incident.environment)
              .filter(([, v]) => v)
              .map(([k, v]) => (
                <span key={k}>
                  <span className="text-neutral-400">{k}:</span> {String(v)}
                </span>
              ))}
          </div>
        </div>
      )}

      {incident.attempts.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-400">
            Failed Attempts
          </h3>
          <ol className="mt-1 flex flex-col gap-2">
            {incident.attempts.map((attempt) => (
              <li key={attempt.id} className="text-sm">
                <span className="font-medium">{attempt.action}</span>
                {attempt.result && (
                  <span className="text-neutral-400"> — {attempt.result}</span>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}

      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-400">
          Attachments
        </h3>
        <div className="mt-1 flex flex-col gap-1">
          {attachmentsQuery.data?.map((a) => (
            <a
              key={a.id}
              href={`/api/attachments/${a.id}/content`}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-blue-600 hover:underline dark:text-blue-400"
            >
              {a.filename} ({(a.size_bytes / 1024).toFixed(1)} KB)
            </a>
          ))}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (file) uploadMutation.mutate(file);
          }}
          className="mt-2 flex items-center gap-2"
        >
          <input
            type="file"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-sm"
          />
          <button
            type="submit"
            disabled={!file || uploadMutation.isPending}
            className="rounded-md border border-neutral-300 px-2 py-1 text-xs disabled:opacity-40 dark:border-neutral-700"
          >
            Upload
          </button>
        </form>
      </div>

      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-400">
          Related Incidents
        </h3>
        <div className="mt-1 flex flex-col gap-1">
          {relatedQuery.data?.length === 0 && (
            <p className="text-sm text-neutral-400">None linked yet.</p>
          )}
          {relatedQuery.data?.map((r) => (
            <Link
              key={r.incident_id}
              to={`/incidents/${r.incident_id}`}
              className="text-sm text-blue-600 hover:underline dark:text-blue-400"
            >
              {r.title ?? `Incident #${r.incident_id}`}
              <span className="ml-2 text-xs text-neutral-400">{r.depth}-hop</span>
            </Link>
          ))}
        </div>

        {suggestedQuery.data && suggestedQuery.data.length > 0 && (
          <div className="mt-3">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-neutral-400">
              Suggested (by similarity)
            </h4>
            <div className="mt-1 flex flex-col gap-1">
              {suggestedQuery.data.map((s) => (
                <div key={s.incident_id} className="flex items-center gap-2 text-sm">
                  <span>{s.title ?? `Incident #${s.incident_id}`}</span>
                  <span className="text-xs text-neutral-400">
                    {(s.similarity * 100).toFixed(0)}% similar
                  </span>
                  <button
                    onClick={() => linkMutation.mutate(s.incident_id)}
                    disabled={linkMutation.isPending}
                    className="rounded border border-neutral-300 px-1.5 py-0.5 text-xs disabled:opacity-40 dark:border-neutral-700"
                  >
                    Link
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
