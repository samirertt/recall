import { useEffect, useState } from "react";

interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
}

/** On-page search input. Global Ctrl/Cmd+K opens the CommandPalette instead (it
 * works from any route, not just this one) — see components/CommandPalette.tsx. */
export default function SearchBar({ value, onChange }: SearchBarProps) {
  const [local, setLocal] = useState(value);

  useEffect(() => {
    const id = setTimeout(() => onChange(local), 200);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [local]);

  return (
    <input
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      placeholder="What engineering problem are you having?"
      aria-label="Search engineering incidents"
      className="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900"
    />
  );
}
