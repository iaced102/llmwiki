"""
Embedding model catalog — code-level whitelist of supported embedding models.

This is the single source of truth for which embedding models the system
supports. Admins choose from this catalog (via the settings UI); they cannot
type free-form model IDs or dimensions, which previously caused two classes of
production bugs:

  1. Misspelled model_id → API call fails or silently uses a different model.
  2. Dimension mismatch → vectors stored with wrong shape, search broken.

Adding a new model means adding an entry here AND adding a per-dimension
SQLAlchemy table + Alembic migration if its `dimension` is not already
supported. See plan: app/ai/embedding_catalog.py for context.
"""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class EmbeddingModelSpec:
    id: str                    # canonical: "<provider>/<model_id>", e.g. "openai/text-embedding-3-small"
    provider: str              # matches ProviderType: "openai" | "google" | ...
    model_id: str              # ID sent to the provider API: "text-embedding-3-small"
    dimension: int             # output vector dim — must match a wiki_page_embeddings_<dim> table
    max_input_tokens: int      # provider's per-request token cap (used for chunking budgets)
    label: str                 # short label shown in UI
    cost_per_1m_tokens: float | None  # USD per 1M input tokens; None = unknown / free
    notes: str | None = None   # short hint for admins


# All entries here MUST have a `dimension` value that has a matching
# `wiki_page_embeddings_<dim>` table in the database. Currently supported
# dimensions: 768, 1024, 1536, 3072.
EMBEDDING_CATALOG: dict[str, EmbeddingModelSpec] = {
    # --- Google Gemini ---
    # Both Gemini embedding models support flexible output dim (128–3072).
    # We pin to 3072 (highest recommended) so the schema column type matches.
    # If you want the cheaper 1536/768 tiers, add a second spec entry — the
    # provider call already passes `output_dimensionality` from spec.dimension.
    "google/gemini-embedding-001": EmbeddingModelSpec(
        id="google/gemini-embedding-001",
        provider="google",
        model_id="gemini-embedding-001",
        dimension=3072,
        max_input_tokens=2048,
        label="Gemini Embedding 001 (3072d)",
        cost_per_1m_tokens=0.15,
        notes="Text-only. Stable since June 2025. Strong multilingual incl. Vietnamese.",
    ),
    "google/gemini-embedding-2": EmbeddingModelSpec(
        id="google/gemini-embedding-2",
        provider="google",
        model_id="gemini-embedding-2",
        dimension=3072,
        max_input_tokens=8192,
        label="Gemini Embedding 2 (3072d, multimodal)",
        cost_per_1m_tokens=0.15,
        notes="Multimodal (text, image, video, audio, PDF). 8K input window. Stable Apr 2026.",
    ),
    # --- OpenAI ---
    "openai/text-embedding-3-small": EmbeddingModelSpec(
        id="openai/text-embedding-3-small",
        provider="openai",
        model_id="text-embedding-3-small",
        dimension=1536,
        max_input_tokens=8191,
        label="OpenAI text-embedding-3-small (1536d)",
        cost_per_1m_tokens=0.02,
        notes="Best price/performance on OpenAI side.",
    ),
    "openai/text-embedding-3-large": EmbeddingModelSpec(
        id="openai/text-embedding-3-large",
        provider="openai",
        model_id="text-embedding-3-large",
        dimension=3072,
        max_input_tokens=8191,
        label="OpenAI text-embedding-3-large (3072d)",
        cost_per_1m_tokens=0.13,
        notes="Highest quality OpenAI embedding. ~6.5x cost of 3-small.",
    ),
    # --- LiteLLM (universal proxy) ---
    # Single entry; dimension is fixed at 1536 (most common).
    # The actual model string is configured freeform in admin settings
    # (embedding_litellm_model_id). Choose this entry only if your LiteLLM
    # model returns 1536-dim vectors.
    "litellm/custom": EmbeddingModelSpec(
        id="litellm/custom",
        provider="litellm",
        model_id="custom",  # overridden at runtime via embedding_litellm_model_id
        dimension=1536,
        max_input_tokens=8191,
        label="LiteLLM (custom)",
        cost_per_1m_tokens=None,
        notes="Any 1536-dim embedding model via LiteLLM proxy. Configure URL, API key, and model name in settings.",
    ),
}


SUPPORTED_DIMENSIONS: tuple[int, ...] = tuple(
    sorted({s.dimension for s in EMBEDDING_CATALOG.values()})
)


class UnknownEmbeddingModel(KeyError):
    """Raised when a spec_id is not in the catalog."""


def get_spec(spec_id: str) -> EmbeddingModelSpec:
    try:
        return EMBEDDING_CATALOG[spec_id]
    except KeyError as e:
        raise UnknownEmbeddingModel(
            f"Unknown embedding model spec_id={spec_id!r}. "
            f"Valid IDs: {sorted(EMBEDDING_CATALOG.keys())}"
        ) from e


def list_specs() -> list[EmbeddingModelSpec]:
    return list(EMBEDDING_CATALOG.values())


def list_specs_by_provider(provider: str) -> list[EmbeddingModelSpec]:
    return [s for s in EMBEDDING_CATALOG.values() if s.provider == provider]


def specs_for_dimension(dimension: int) -> Iterable[EmbeddingModelSpec]:
    return (s for s in EMBEDDING_CATALOG.values() if s.dimension == dimension)
