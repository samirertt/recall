const BASE = "/api";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(res.status, body || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// --- Types (mirroring backend/app/schemas) ---

export type IncidentStatus =
  | "unresolved"
  | "investigating"
  | "solved"
  | "abandoned"
  | "obsolete";
export type Severity = "low" | "medium" | "high" | "critical";

export interface EnvironmentIn {
  operating_system?: string | null;
  os_version?: string | null;
  architecture?: string | null;
  hardware?: string | null;
  cpu?: string | null;
  gpu?: string | null;
  device?: string | null;
  firmware?: string | null;
  driver_versions?: string | null;
  language?: string | null;
  runtime?: string | null;
  compiler?: string | null;
  framework?: string | null;
  library?: string | null;
  library_versions?: string | null;
  container?: string | null;
  configuration?: string | null;
}

export interface Attempt {
  id: number;
  order: number;
  action: string;
  command?: string | null;
  hypothesis?: string | null;
  result?: string | null;
  why_failed?: string | null;
  created_at: string;
}

export interface Incident {
  id: number;
  raw_problem: string;
  raw_solution: string | null;
  title: string | null;
  normalized_problem: string | null;
  symptoms: string | null;
  root_cause: string | null;
  solution: string | null;
  explanation: string | null;
  why_solution_worked: string | null;
  lesson_learned: string | null;
  status: IncidentStatus;
  severity: Severity | null;
  confidence: number | null;
  needs_ai_review: boolean;
  solved_at: string | null;
  last_verified_at: string | null;
  created_at: string;
  updated_at: string;
  environment: EnvironmentIn | null;
  attempts: Attempt[];
}

export interface IncidentListItem {
  id: number;
  title: string | null;
  status: IncidentStatus;
  severity: Severity | null;
  needs_ai_review: boolean;
  created_at: string;
  updated_at: string;
}

export interface PossibleDuplicate {
  incident_id: number;
  title: string | null;
  similarity_hint: string;
}

export interface IncidentCreateResponse {
  incident: Incident;
  possible_duplicates: PossibleDuplicate[];
}

export interface SearchSignals {
  lexical?: Record<string, unknown> | null;
  trigram?: Record<string, unknown> | null;
  vector?: Record<string, unknown> | null;
  exact_match?: Record<string, unknown> | null;
  attachment?: Record<string, unknown> | null;
}

export interface SearchResult {
  incident_id: number;
  title: string | null;
  status: IncidentStatus;
  fused_score: number;
  signals: SearchSignals;
  snippet: string | null;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  degraded: { vector_search: boolean };
}

export interface Attachment {
  id: number;
  incident_id: number;
  filename: string;
  mime_type: string | null;
  size_bytes: number;
  sha256: string;
  created_at: string;
}

// --- API calls ---

export const api = {
  health: () => request<Record<string, unknown>>("/health"),

  listIncidents: (status?: IncidentStatus) =>
    request<IncidentListItem[]>(`/incidents${status ? `?status=${status}` : ""}`),

  getIncident: (id: number) => request<Incident>(`/incidents/${id}`),

  createIncident: (data: { raw_problem: string; raw_solution?: string }) =>
    request<IncidentCreateResponse>("/incidents", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  quickCapture: (raw_text: string) =>
    request<IncidentCreateResponse>("/incidents/quick-capture", {
      method: "POST",
      body: JSON.stringify({ raw_text }),
    }),

  updateIncident: (id: number, data: Record<string, unknown>) =>
    request<Incident>(`/incidents/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  archiveIncident: (id: number) =>
    request<Incident>(`/incidents/${id}/archive`, { method: "POST" }),

  search: (q: string, limit = 20) =>
    request<SearchResponse>(`/search?q=${encodeURIComponent(q)}&limit=${limit}`),

  listAttachments: (incidentId: number) =>
    request<Attachment[]>(`/incidents/${incidentId}/attachments`),

  uploadAttachment: async (incidentId: number, file: File): Promise<Attachment> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/incidents/${incidentId}/attachments`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return res.json();
  },
};
