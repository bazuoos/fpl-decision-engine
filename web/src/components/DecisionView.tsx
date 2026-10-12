import type {
  DecisionReadResponse as DecisionEnvelope,
  GameweekDecisionPlayer as Player,
} from "../api/generated-contracts";

function names(ids: number[], players: Map<number, Player>): string {
  return ids.map((id) => players.get(id)?.name ?? `Player ${id}`).join(", ");
}

function selectionWarningText(code: string): string {
  if (code === "appearance_only_policy_admits_incomplete_projections") {
    return "The selected squad includes an incomplete projection admitted under the appearance-only policy.";
  }
  return `The verified decision includes an unrecognized warning code (${code}).`;
}

export function DecisionView({ envelope }: { envelope: DecisionEnvelope }) {
  const decision = envelope.payload;
  const action = decision.recommended_action;
  const selection = action.selection;
  const players = new Map(decision.players.map((player) => [player.element_id, player]));
  const captain = players.get(selection.captain)?.name ?? `Player ${selection.captain}`;
  const vice = players.get(selection.vice_captain)?.name ?? `Player ${selection.vice_captain}`;
  const knownV01Scope = decision.engine.model_id === "xfp_v01" && decision.engine.model_scope === "modeled_components_only";
  const selectionWarningCodes = selection.warnings.filter(
    (code) => !knownV01Scope || code !== "xfp_v01_models_appearance_goals_assists_only",
  );

  return (
    <article className="decision-card" aria-labelledby="decision-title">
      <div className="trust" role="status">Verified Engine v1 decision</div>
      <p className="eyebrow">{decision.season} · Gameweek {decision.target_gameweek}</p>
      <h2 id="decision-title">{action.action_type}</h2>
      {action.action_type === "TRANSFER" && (
        <p className="transfer">{action.outgoing?.name} → {action.incoming?.name}</p>
      )}
      <dl className="summary-grid">
        <div><dt>Formation</dt><dd>{selection.formation}</dd></div>
        <div><dt>Captain</dt><dd>{captain}</dd></div>
        <div><dt>Vice-captain</dt><dd>{vice}</dd></div>
        <div><dt>Objective</dt><dd>{selection.objective.total_xfp.toFixed(2)} xFP</dd></div>
      </dl>
      <section className="decision-caveats" aria-labelledby="decision-caveats-title">
        <h3 id="decision-caveats-title">How to read this decision</h3>
        <p>Reliability checks are diagnostic only. They do not change the verified recommendation or measure the chance it will succeed.</p>
        <p>Model: {decision.engine.model_id} · Scope: {decision.engine.model_scope}</p>
        {knownV01Scope && (
          <p>xFP v0.1 models appearance, goals and assists only. It omits other FPL scoring such as clean sheets, saves, bonus and defensive contributions; the xFP total is not a complete points forecast.</p>
        )}
        {decision.reliability.warnings.length > 0 ? (
          <ul aria-label="Reliability warnings">
            {decision.reliability.warnings.map((warning, index) => (
              <li key={`${warning.code}-${index}`}>{warning.message}</li>
            ))}
          </ul>
        ) : (
          <p>No reliability warnings were recorded. That does not mean the prediction is certain.</p>
        )}
        {selectionWarningCodes.length > 0 && (
          <div>
            <h4>Squad warnings</h4>
            <ul aria-label="Squad warnings">
              {selectionWarningCodes.map((code, index) => <li key={`${code}-${index}`}>{selectionWarningText(code)}</li>)}
            </ul>
          </div>
        )}
      </section>
      <section>
        <h3>Starting XI</h3>
        <p>{names(selection.starting_xi, players)}</p>
      </section>
      <section>
        <h3>Bench</h3>
        <p>{names(selection.bench, players)}</p>
      </section>
      <details>
        <summary>Artifact identity</summary>
        <dl className="identity">
          <dt>Decision</dt><dd>{envelope.artifact_identity.semantic_id}</dd>
          <dt>Preparation</dt><dd>{envelope.artifact_identity.preparation_id}</dd>
          <dt>Artifact SHA-256</dt><dd>{envelope.artifact_identity.sha256}</dd>
          <dt>Deadline</dt><dd>{decision.frozen_deadline}</dd>
        </dl>
      </details>
    </article>
  );
}
