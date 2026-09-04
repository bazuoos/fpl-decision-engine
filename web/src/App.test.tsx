import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { syntheticDecision } from "./test/fixture";

function renderApp(route = "/") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[route]}><App /></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("trusted decision presentation", () => {
  it("starts without discovering a latest decision", () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    renderApp();
    expect(screen.getByText(/No decision selected/)).toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("renders canonical API values without deriving formation or captaincy", async () => {
    const supplied = structuredClone(syntheticDecision);
    supplied.payload.recommended_action.selection.formation = "3-5-2";
    supplied.payload.recommended_action.selection.captain = 8;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => supplied }));
    renderApp(`/?decision_id=${supplied.artifact_identity.semantic_id}`);
    expect(await screen.findByText("3-5-2")).toBeInTheDocument();
    expect(screen.getAllByText("Synthetic Player 08").length).toBeGreaterThan(0);
    expect(screen.getByText("48.00 xFP")).toBeInTheDocument();
  });

  it("distinguishes missing from invalid trusted evidence", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ ok: false, json: async () => ({ code: "NOT_FOUND", detail: "missing", status: 404, title: "failed" }) })
      .mockResolvedValueOnce({ ok: false, json: async () => ({ code: "HASH_MISMATCH", detail: "invalid", status: 422, title: "failed" }) });
    vi.stubGlobal("fetch", fetcher);
    const first = renderApp(`/?decision_id=decision_${"a".repeat(64)}`);
    expect(await screen.findByText("No decision exists for this ID")).toBeInTheDocument();
    first.unmount();
    renderApp(`/?decision_id=decision_${"b".repeat(64)}`);
    expect(await screen.findByText("Decision cannot be trusted")).toBeInTheDocument();
  });

  it("navigates only to the explicit identity supplied by the user", async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => syntheticDecision });
    vi.stubGlobal("fetch", fetcher);
    renderApp();
    fireEvent.change(screen.getByLabelText("Decision ID"), { target: { value: syntheticDecision.artifact_identity.semantic_id } });
    fireEvent.click(screen.getByRole("button", { name: "Open decision" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(String(fetcher.mock.calls[0][0])).toContain(syntheticDecision.artifact_identity.semantic_id);
  });
});
