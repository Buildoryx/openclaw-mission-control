/**
 * Deliberation module component exports.
 *
 * Re-exports all public components and type utilities used by the
 * deliberation pages under `app/boards/[boardId]/deliberations/`.
 */

export { DeliberationCard } from "./DeliberationCard";
export { DeliberationComposer } from "./DeliberationComposer";
export { EntryCard } from "./EntryCard";
export { SynthesisPanel } from "./SynthesisPanel";

export type {
  AgentTrackRecord,
  ConsensusLevel,
  DeliberationCreate,
  DeliberationEntryCreate,
  DeliberationEntryRead,
  DeliberationRead,
  DeliberationStatus,
  DeliberationSynthesisCreate,
  DeliberationSynthesisRead,
  EntryType,
  EpisodicMemoryRead,
  PaginatedResponse,
} from "./types";

export {
  CONSENSUS_LABELS,
  CONSENSUS_VARIANTS,
  DELIBERATION_STATUS_LABELS,
  DELIBERATION_STATUS_VARIANTS,
  ENTRY_TYPE_LABELS,
  abandonDeliberation,
  advanceDeliberation,
  createDeliberation,
  createEntry,
  createSynthesis,
  deliberationStreamUrl,
  formatConfidence,
  formatDuration,
  formatTimestamp,
  getDeliberation,
  getSynthesis,
  isTerminalStatus,
  listDeliberations,
  listEntries,
  promoteSynthesis,
} from "./types";
