import type { DecisionEnvelope } from "../api/contracts";

const players = Array.from({ length: 15 }, (_, index) => ({
  element_id: index + 1,
  name: `Synthetic Player ${String(index + 1).padStart(2, "0")}`,
  position: (["GK", "DEF", "MID", "FWD"] as const)[Math.min(3, Math.floor(index / 4))],
}));

export const syntheticDecision: DecisionEnvelope = {
  api_version: "1.0",
  artifact_identity: {
    artifact_type: "GameweekDecision",
    artifact_schema_version: "1.0.0",
    semantic_id: `decision_${"a".repeat(64)}`,
    preparation_id: `prep_${"b".repeat(64)}`,
    sha256: "c".repeat(64),
    final_manifest_sha256: "d".repeat(64),
  },
  trust: { state: "VERIFIED", reader_version: "synthetic-reader-v1", complete_chain_validated: true },
  payload: {
    schema_name: "GameweekDecision",
    schema_version: "1.0.0",
    season: "2099-00",
    target_gameweek: 2,
    frozen_deadline: "2099-08-28T17:30:00Z",
    players,
    recommended_action: {
      action_type: "TRANSFER",
      outgoing: { element_id: 15, name: "Synthetic Player 15" },
      incoming: { element_id: 14, name: "Synthetic Player 14" },
      objective_gain_vs_roll_xfp: 1.25,
      selection: {
        starting_xi: [1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14],
        bench: [2, 7, 12, 15],
        captain: 13,
        vice_captain: 8,
        formation: "4-4-2",
        objective: { base_xi_xfp: 42, captain_bonus_xfp: 6, total_xfp: 48 },
      },
    },
  },
};
