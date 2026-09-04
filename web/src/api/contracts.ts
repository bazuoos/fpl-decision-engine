export type Player = {
  element_id: number;
  name: string;
  position: "GK" | "DEF" | "MID" | "FWD";
};

export type Selection = {
  starting_xi: number[];
  bench: number[];
  captain: number;
  vice_captain: number;
  formation: string;
  objective: {
    base_xi_xfp: number;
    captain_bonus_xfp: number;
    total_xfp: number;
  };
};

export type RecommendedAction = {
  action_type: "ROLL" | "TRANSFER";
  outgoing?: { element_id: number; name: string };
  incoming?: { element_id: number; name: string };
  objective_gain_vs_roll_xfp: number;
  selection: Selection;
};

export type GameweekDecision = {
  schema_name: "GameweekDecision";
  schema_version: "1.0.0";
  season: string;
  target_gameweek: number;
  frozen_deadline: string;
  players: Player[];
  recommended_action: RecommendedAction;
};

export type DecisionEnvelope = {
  api_version: "1.0";
  artifact_identity: {
    artifact_type: "GameweekDecision";
    artifact_schema_version: "1.0.0";
    semantic_id: string;
    preparation_id: string;
    sha256: string;
    final_manifest_sha256: string;
  };
  trust: {
    state: "VERIFIED";
    reader_version: string;
    complete_chain_validated: true;
  };
  payload: GameweekDecision;
};

export type ProblemDetails = {
  status: number;
  code: string;
  title: string;
  detail: string;
};
