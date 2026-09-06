import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";

type Mode = "structured" | "quick";

/** Section 18/19: zero-friction capture. Only a problem description is required;
 * everything else can be added later via the incident detail page. */
export default function CaptureForm() {
  const [mode, setMode] = useState<Mode>("structured");
  const [problem, setProblem] = useState("");
  const [solution, setSolution] = useState("");
  const [quickText, setQuickText] = useState("");
  const problemRef = useRef<HTMLTextAreaElement>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  useEffect(() => {
    function handleKeydown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "n") {
        e.preventDefault();
        problemRef.current?.focus();
      }
    }
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, []);

  const createMutation = useMutation({
    mutationFn: () => api.createIncident({ raw_problem: problem, raw_solution: solution || undefined }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["incidents"] });
      setProblem("");
      setSolution("");
      navigate(`/incidents/${res.incident.id}`);
    },
  });

  const quickMutation = useMutation({
    mutationFn: () => api.quickCapture(quickText),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["incidents"] });
      setQuickText("");
      navigate(`/incidents/${res.incident.id}`);
    },
  });

  const busy = createMutation.isPending || quickMutation.isPending;

  return (
    <div className="rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <div className="mb-3 flex gap-4 text-sm">
        <button
          type="button"
          onClick={() => setMode("structured")}
          className={mode === "structured" ? "font-semibold" : "text-neutral-400"}
        >
          New Incident
        </button>
        <button
          type="button"
          onClick={() => setMode("quick")}
          className={mode === "quick" ? "font-semibold" : "text-neutral-400"}
        >
          Quick Capture
        </button>
      </div>

      {mode === "structured" ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (problem.trim()) createMutation.mutate();
          }}
          className="flex flex-col gap-3"
        >
          <label className="text-xs font-medium text-neutral-500">What happened?</label>
          <textarea
            ref={problemRef}
            value={problem}
            onChange={(e) => setProblem(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                if (problem.trim()) createMutation.mutate();
              }
            }}
            rows={3}
            placeholder="Describe the problem..."
            className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900"
          />
          <label className="text-xs font-medium text-neutral-500">
            How did you solve it? (optional)
          </label>
          <textarea
            value={solution}
            onChange={(e) => setSolution(e.target.value)}
            rows={2}
            placeholder="Leave blank if unresolved..."
            className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900"
          />
          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={busy || !problem.trim()}
              className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-neutral-100 dark:text-neutral-900"
            >
              Save
            </button>
            <span className="text-xs text-neutral-400">⌘Enter to save · ⌘N to focus</span>
          </div>
        </form>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (quickText.trim()) quickMutation.mutate();
          }}
          className="flex flex-col gap-3"
        >
          <textarea
            value={quickText}
            onChange={(e) => setQuickText(e.target.value)}
            rows={4}
            placeholder="Paste anything — save now, structure it later..."
            className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900"
          />
          <button
            type="submit"
            disabled={busy || !quickText.trim()}
            className="w-fit rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-neutral-100 dark:text-neutral-900"
          >
            Save Immediately
          </button>
        </form>
      )}

      {(createMutation.isError || quickMutation.isError) && (
        <p className="mt-2 text-sm text-red-600">
          Failed to save: {String((createMutation.error ?? quickMutation.error) as Error)}
        </p>
      )}
    </div>
  );
}
