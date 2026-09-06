"""AI provider abstraction (Phase 10; docs/RESEARCH.md § AI Provider Abstraction).

One shared `IncidentExtraction` Pydantic schema (app/schemas/extraction.py) is the
source of truth every adapter targets — thin per-vendor adapters, not three parallel
hand-maintained schemas. `HeuristicProvider` is the zero-LLM fallback that keeps the
system fully functional with no AI dependency at all (Section 21/33).
"""

import os
from typing import Protocol

from app.core.config import get_settings
from app.models.enums import ProvenanceBasis
from app.schemas.extraction import ExtractionResult, FieldProvenance, IncidentExtraction
from app.services.ai.verification import verify_field, verify_list_field

PROMPT_VERSION = "v1"
SCHEMA_VERSION = "v1"

SYSTEM_PROMPT = (
    "You extract structured engineering-incident knowledge from a raw problem/solution "
    "report. Extract only what the text actually states or clearly implies. Never invent "
    "a hostname, version, date, root cause, or fact that is not present in the text. If "
    "something cannot be determined, leave it null (or an empty list for tags/"
    "technologies) rather than guessing."
)


class AIProvider(Protocol):
    name: str
    model: str

    def is_available(self) -> bool: ...
    async def extract(self, incident_text: str) -> ExtractionResult: ...


def _build_provenance(fields: IncidentExtraction, raw_text: str) -> dict[str, FieldProvenance]:
    provenance: dict[str, FieldProvenance] = {}
    for field_name in (
        "title",
        "normalized_problem",
        "symptoms",
        "root_cause",
        "solution",
        "why_solution_worked",
        "lesson_learned",
    ):
        provenance[field_name] = verify_field(field_name, getattr(fields, field_name), raw_text)
    for field_name in ("tags", "technologies"):
        values = getattr(fields, field_name)
        per_value = verify_list_field(field_name, values, raw_text)
        # Store as one FieldProvenance summarizing the list — worst-case basis, min confidence
        if per_value:
            worst = min(per_value, key=lambda p: p.confidence or 0)
            provenance[field_name] = FieldProvenance(
                value=", ".join(values), basis=worst.basis, confidence=worst.confidence
            )
        else:
            provenance[field_name] = FieldProvenance(
                value=None, basis=ProvenanceBasis.unknown, confidence=None
            )
    return provenance


class HeuristicProvider:
    """Zero-LLM fallback (Section 21/33): keeps the system fully functional with no AI
    dependency. First non-blank line as title; everything else left unset."""

    name = "heuristic"
    model = "heuristic-v1"

    def is_available(self) -> bool:
        return True

    async def extract(self, incident_text: str) -> ExtractionResult:
        title = "untitled incident"
        for line in incident_text.splitlines():
            stripped = line.strip()
            if stripped:
                title = stripped[:120]
                break
        fields = IncidentExtraction(title=title)
        provenance = {
            "title": FieldProvenance(value=title, basis=ProvenanceBasis.explicit, confidence=1.0)
        }
        return ExtractionResult(
            fields=fields,
            provenance=provenance,
            provider=self.name,
            model=self.model,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
        )


class ClaudeProvider:
    name = "claude"

    def __init__(self, api_key: str, model: str = "claude-sonnet-5") -> None:
        self.model = model
        self._api_key = api_key

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def extract(self, incident_text: str) -> ExtractionResult:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self._api_key)
        response = await client.messages.parse(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": incident_text}],
            output_format=IncidentExtraction,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise ExtractionRefused(f"Claude declined or returned no structured output "
                                     f"(stop_reason={response.stop_reason})")
        fields = response.parsed_output
        return ExtractionResult(
            fields=fields,
            provenance=_build_provenance(fields, incident_text),
            provider=self.name,
            model=self.model,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            raw_response=response.model_dump_json(),
        )


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, api_key: str | None, base_url: str | None, model: str = "gpt-5") -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = base_url

    def is_available(self) -> bool:
        return bool(self._api_key or self._base_url)

    async def extract(self, incident_text: str) -> ExtractionResult:
        import openai

        client = openai.AsyncOpenAI(api_key=self._api_key or "not-needed", base_url=self._base_url)
        completion = await client.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": incident_text},
            ],
            response_format=IncidentExtraction,
        )
        fields = completion.choices[0].message.parsed
        if fields is None:
            raise ExtractionRefused("Model returned no parseable structured output")
        return ExtractionResult(
            fields=fields,
            provenance=_build_provenance(fields, incident_text),
            provider=self.name,
            model=self.model,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            raw_response=completion.model_dump_json(),
        )


class ExtractionRefused(Exception):
    pass


def get_ai_provider() -> AIProvider:
    """Never raises, never returns None — always at least HeuristicProvider, so
    callers don't need their own fallback logic (Section 21/33/52)."""
    settings = get_settings()
    try:
        if settings.ai_provider == "claude":
            api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                return ClaudeProvider(api_key=api_key)
        elif settings.ai_provider == "openai_compatible":
            api_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
            if api_key or settings.openai_api_base:
                return OpenAICompatibleProvider(
                    api_key=api_key, base_url=settings.openai_api_base
                )
    except Exception:
        pass
    return HeuristicProvider()
