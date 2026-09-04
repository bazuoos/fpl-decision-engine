import type { DecisionEnvelope, ProblemDetails } from "./contracts";

export class ApiProblem extends Error {
  constructor(readonly problem: ProblemDetails) {
    super(problem.detail);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function acceptDecisionEnvelope(value: unknown): DecisionEnvelope {
  if (!isRecord(value) || value.api_version !== "1.0") {
    throw new Error("Unsupported API response");
  }
  const identity = value.artifact_identity;
  const trust = value.trust;
  const payload = value.payload;
  if (
    !isRecord(identity) ||
    identity.artifact_type !== "GameweekDecision" ||
    identity.artifact_schema_version !== "1.0.0" ||
    !isRecord(trust) ||
    trust.state !== "VERIFIED" ||
    trust.complete_chain_validated !== true ||
    !isRecord(payload) ||
    payload.schema_name !== "GameweekDecision" ||
    payload.schema_version !== "1.0.0"
  ) {
    throw new Error("Decision response is not trusted or supported");
  }
  return value as DecisionEnvelope;
}

export async function fetchDecision(decisionId: string): Promise<DecisionEnvelope> {
  const response = await fetch(`/api/v1/decisions/${encodeURIComponent(decisionId)}`);
  const body: unknown = await response.json();
  if (!response.ok) {
    if (isRecord(body) && typeof body.code === "string") {
      throw new ApiProblem(body as ProblemDetails);
    }
    throw new Error("The decision service returned an unreadable error");
  }
  return acceptDecisionEnvelope(body);
}
