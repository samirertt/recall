import { Link, Route, Routes } from "react-router-dom";
import CommandPalette from "./components/CommandPalette";
import IncidentDetailPage from "./pages/IncidentDetailPage";
import HomePage from "./pages/HomePage";

export default function App() {
  return (
    <div className="min-h-screen bg-white text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100">
      <header className="border-b border-neutral-200 dark:border-neutral-800">
        <div className="mx-auto flex max-w-5xl items-center gap-4 px-4 py-3">
          <Link to="/" className="font-mono text-sm font-semibold tracking-tight">
            engineering-memory
          </Link>
          <span className="text-xs text-neutral-400">
            local-first engineering incident knowledge
          </span>
          <kbd className="ml-auto rounded border border-neutral-300 px-1.5 py-0.5 text-[10px] text-neutral-400 dark:border-neutral-700">
            ⌘K to search
          </kbd>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/incidents/:id" element={<IncidentDetailPage />} />
        </Routes>
      </main>
      <CommandPalette />
    </div>
  );
}
