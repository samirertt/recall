import { Command } from "cmdk";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";

/** Section 50: a real command palette, not just an on-page search-bar focus — this
 * works from anywhere in the app (e.g. the incident detail page has no visible
 * search box of its own), matching Ctrl/Cmd+K's usual meaning. */
export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<{ incident_id: number; title: string | null }[]>([]);
  const navigate = useNavigate();

  useEffect(() => {
    function handleKeydown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    }
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, []);

  useEffect(() => {
    if (!open || !query.trim()) {
      setResults([]);
      return;
    }
    const id = setTimeout(() => {
      api
        .search(query, 8)
        .then((res) => setResults(res.results))
        .catch(() => setResults([]));
    }, 150);
    return () => clearTimeout(id);
  }, [query, open]);

  function go(path: string) {
    navigate(path);
    setOpen(false);
    setQuery("");
  }

  return (
    <Command.Dialog
      open={open}
      onOpenChange={setOpen}
      label="Command palette"
      overlayClassName="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
      contentClassName="fixed left-1/2 top-24 z-50 w-full max-w-lg -translate-x-1/2 overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-2xl outline-none dark:border-neutral-800 dark:bg-neutral-900"
    >
      <Command.Input
        value={query}
        onValueChange={setQuery}
        placeholder="Search incidents or run a command..."
        className="w-full border-b border-neutral-200 bg-transparent px-4 py-3 text-sm outline-none dark:border-neutral-800"
      />
      <Command.List className="max-h-80 overflow-y-auto p-2">
        <Command.Empty className="px-2 py-4 text-center text-sm text-neutral-400">
          {query.trim() ? "No matching incidents." : "Type to search, or pick an action below."}
        </Command.Empty>

        {results.length > 0 && (
          <Command.Group
            heading="Incidents"
            className="px-2 py-1 text-xs font-medium uppercase tracking-wide text-neutral-400"
          >
            {results.map((r) => (
              <Command.Item
                key={r.incident_id}
                onSelect={() => go(`/incidents/${r.incident_id}`)}
                className="cursor-pointer rounded px-2 py-2 text-sm data-[selected=true]:bg-neutral-100 dark:data-[selected=true]:bg-neutral-800"
              >
                {r.title ?? `Incident #${r.incident_id}`}
              </Command.Item>
            ))}
          </Command.Group>
        )}

        {!query.trim() && (
          <Command.Group
            heading="Actions"
            className="px-2 py-1 text-xs font-medium uppercase tracking-wide text-neutral-400"
          >
            <Command.Item
              onSelect={() => go("/")}
              className="cursor-pointer rounded px-2 py-2 text-sm data-[selected=true]:bg-neutral-100 dark:data-[selected=true]:bg-neutral-800"
            >
              New incident / quick capture (⌘N once on the page)
            </Command.Item>
            <Command.Item
              onSelect={() => go("/")}
              className="cursor-pointer rounded px-2 py-2 text-sm data-[selected=true]:bg-neutral-100 dark:data-[selected=true]:bg-neutral-800"
            >
              Go to dashboard / recent incidents
            </Command.Item>
          </Command.Group>
        )}
      </Command.List>
    </Command.Dialog>
  );
}
