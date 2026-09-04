import type { DecisionEnvelope, Player } from "../api/contracts";

function names(ids: number[], players: Map<number, Player>): string {
  return ids.map((id) => players.get(id)?.name ?? `Player ${id}`).join(", ");
}

export function DecisionView({ envelope }: { envelope: DecisionEnvelope }) {
  const decision = envelope.payload;
  const action = decision.recommended_action;
  const selection = action.selection;
  const players = new Map(decision.players.map((player) => [player.element_id, player]));
  const captain = players.get(selection.captain)?.name ?? `Player ${selection.captain}`;
  const vice = players.get(selection.vice_captain)?.name ?? `Player ${selection.vice_captain}`;

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
