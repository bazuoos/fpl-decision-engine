import type {
  DecisionReadResponse as DecisionEnvelope,
  GameweekDecision,
  GameweekDecisionPlayer as Player,
  GameweekDecisionSelection as Selection,
} from "../api/generated-contracts";

const positions = [
  "GK", "GK",
  "DEF", "DEF", "DEF", "DEF", "DEF",
  "MID", "MID", "MID", "MID", "MID", "MID",
  "FWD", "FWD", "FWD",
] as const;

const players: Player[] = positions.map((position, index) => ({
  element_id: index + 1,
  expected_minutes: 90,
  name: `Synthetic Player ${String(index + 1).padStart(2, "0")}`,
  position,
  prediction_complete: true,
  projected_xfp: index + 1,
  projection_state: "valid_projection",
  purchase_price_units: 50,
  selling_price_units: index === 15 ? null : 50,
  team_id: (index % 8) + 1,
  team_name: `Synthetic Team ${(index % 8) + 1}`,
  team_short_name: `S${(index % 8) + 1}`,
}));

const selection = (squad: number[], totalXfp: number): Selection => ({
  bench: [2, 6, 7, 15],
  bench_order_semantics: "goalkeeper_then_outfield",
  captain: 8,
  formation: "3-5-2",
  incomplete_projection_ids: [],
  objective: {
    base_xi_xfp: totalXfp - 8,
    captain_bonus_xfp: 8,
    total_xfp: totalXfp,
  },
  squad,
  starting_xi: [
    1, 3, 4, 5, 8, 9, 10, 11, squad.includes(16) ? 16 : 12, 13, 14,
  ],
  vice_captain: 13,
  warnings: [],
});

const initialSquad = Array.from({ length: 15 }, (_, index) => index + 1);
const resultingSquad = initialSquad.filter((id) => id !== 12).concat(16);

const payload: GameweekDecision = {
  classification: "read_only_validated_single_gameweek_decision",
  engine: {
    decision_artifact_version: "decision-artifact-v1",
    decision_engine_version: "decision-engine-v2",
    decision_policy: "appearance_only_allowed",
    decision_policy_version: "v1",
    model_id: "xfp_v01",
    model_scope: "modeled_components_only",
    reliability_version: "decision-reliability-v1",
  },
  frozen_deadline: "2099-08-28T17:30:00Z",
  generation_timestamp: null,
  generation_timestamp_semantics: "copied_from_upstream_decision_artifact",
  manager_state: {
    bank_units: 10,
    chip_state: "NONE_MODELED",
    current_transfer_cost_points: 0,
    entry_id: 1,
    free_transfers: 1,
    squad: initialSquad,
    verified_provenance: {
      authentication_data_exposed: false,
      manager_specific_selling_prices: true,
      manager_state_source: "synthetic_test_fixture",
      selling_price_source: "synthetic_test_fixture",
    },
  },
  players,
  recommended_action: {
    action_type: "TRANSFER",
    incoming: {
      element_id: 16,
      name: "Synthetic Player 16",
      purchase_price_units: 50,
    },
    objective_gain_vs_roll_xfp: 5,
    outgoing: {
      element_id: 12,
      name: "Synthetic Player 12",
      selling_price_units: 50,
    },
    resulting_bank_units: 10,
    selection: selection(resultingSquad, 55),
    transfer_cost_points: 0,
  },
  reliability: {
    captaincy_materially_changes_gain: false,
    changed_action_count: 0,
    diagnostic_only: true,
    material_players: [],
    official_recommendation_unchanged: true,
    same_exact_action_count: 0,
    sensitivity_results: [],
    sensitivity_view_count: 0,
    warnings: [],
  },
  roll: selection(initialSquad, 50),
  schema_name: "GameweekDecision",
  schema_version: "1.0.0",
  season: "2099-00",
  source_artifacts: Array.from({ length: 8 }, (_, index) => ({
    artifact_name: `synthetic-${index + 1}.json`,
    hash_validated: true,
    role: `synthetic_role_${index + 1}`,
    sha256: String((index + 1) % 10).repeat(64),
  })),
  target_gameweek: 2,
  validation: {
    all_source_hashes_validated: true,
    bench_accounting_passed: true,
    captain_vice_constraints_passed: true,
    reliability_provenance_validated: true,
    squad_legality_passed: true,
    transfer_legality: {
      applicable: true,
      candidate_count: 1,
      passed: true,
      proof: "synthetic fixture",
    },
    xi_legality_passed: true,
  },
};

export const syntheticDecision: DecisionEnvelope = {
  api_version: "1.0",
  artifact_identity: {
    artifact_schema_version: "1.0.0",
    artifact_type: "GameweekDecision",
    final_manifest_sha256: "c".repeat(64),
    preparation_id: `prep_${"b".repeat(64)}`,
    semantic_id: `decision_${"a".repeat(64)}`,
    sha256: "d".repeat(64),
  },
  payload,
  trust: {
    complete_chain_validated: true,
    reader_version: "trusted-artifact-reader-v1",
    state: "VERIFIED",
  },
};
