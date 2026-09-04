import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiProblem, fetchDecision } from "./api/client";
import { DecisionView } from "./components/DecisionView";

export function App() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const selectedId = searchParams.get("decision_id") ?? "";
  const [input, setInput] = useState(selectedId);
  const query = useQuery({
    queryKey: ["decision", selectedId],
    queryFn: () => fetchDecision(selectedId),
    enabled: selectedId.length > 0,
    retry: false,
  });

  function selectDecision(event: FormEvent) {
    event.preventDefault();
    const explicit = input.trim();
    if (explicit) navigate(`/?decision_id=${encodeURIComponent(explicit)}`);
  }

  let content;
  if (!selectedId) {
    content = <p className="state neutral">No decision selected. Enter an explicit decision ID.</p>;
  } else if (query.isPending) {
    content = <p className="state neutral" role="status">Verifying the decision trust chain…</p>;
  } else if (query.error) {
    const notFound = query.error instanceof ApiProblem && query.error.problem.code === "NOT_FOUND";
    content = (
      <div className={`state ${notFound ? "neutral" : "danger"}`} role="alert">
        <strong>{notFound ? "No decision exists for this ID" : "Decision cannot be trusted"}</strong>
        <span>{query.error.message}</span>
      </div>
    );
  } else if (query.data) {
    content = <DecisionView envelope={query.data} />;
  }

  return (
    <main>
      <header>
        <p className="eyebrow">FPL Decision Engine</p>
        <h1>Verified decisions, without a second engine.</h1>
        <p className="lede">This application displays an immutable Engine v1 artifact only after its complete trust chain passes.</p>
      </header>
      <form onSubmit={selectDecision} className="lookup">
        <label htmlFor="decision-id">Decision ID</label>
        <div>
          <input id="decision-id" value={input} onChange={(event) => setInput(event.target.value)} placeholder="decision_…" autoComplete="off" />
          <button type="submit">Open decision</button>
        </div>
      </form>
      {content}
    </main>
  );
}
