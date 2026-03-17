"""Agent memory subsystem for Mission Control.

Three layers:
1. Working Memory (Redis) — ephemeral state during active deliberations
2. Short-Term Memory (Postgres) — agent messages, traces, 90-day retention
3. Long-Term Memory (Postgres + pgvector) — semantic search over board memory

Plus cross-cutting services:
- Embedding Service — text-to-vector encoding (fastembed / openai / noop)
- Message Bus — Redis Streams pub/sub for deliberation events
- Episodic Extraction — pattern learning from concluded deliberations
"""

from app.services.memory.embedding import (
    EmbeddingService,
    FastEmbedService,
    NoopEmbedService,
    embedding_service,
)
from app.services.memory.episodic import (
    EpisodicExtractionService,
    episodic_extraction_service,
)
from app.services.memory.long_term import (
    EpisodicSearchResult,
    LongTermMemoryService,
    MemorySearchResult,
    long_term_memory,
)
from app.services.memory.message_bus import BusEvent, MessageBus, message_bus
from app.services.memory.short_term import ShortTermMemory, short_term_memory
from app.services.memory.working_memory import WorkingMemoryService, working_memory

__all__ = [
    # Embedding
    "EmbeddingService",
    "FastEmbedService",
    "NoopEmbedService",
    "embedding_service",
    # Working memory (Redis ephemeral)
    "WorkingMemoryService",
    "working_memory",
    # Short-term memory (Postgres messages)
    "ShortTermMemory",
    "short_term_memory",
    # Long-term memory (pgvector semantic)
    "LongTermMemoryService",
    "MemorySearchResult",
    "EpisodicSearchResult",
    "long_term_memory",
    # Message bus (Redis Streams)
    "MessageBus",
    "BusEvent",
    "message_bus",
    # Episodic extraction
    "EpisodicExtractionService",
    "episodic_extraction_service",
]
