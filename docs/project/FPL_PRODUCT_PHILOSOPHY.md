# FPL product philosophy

The product intent below is **HUMAN-APPROVED PROJECT POLICY**, confirmed for this
remediation on 2026-09-04, not a fact inferred from implementation. Repository
constraints are identified separately. Neither policy nor this checkpoint
certifies implemented features or statistically validated predictive advantage.

## Product intent (human-approved project policy)

- Improve FPL decisions and maximize expected FPL performance, not validate the
  manager's opinions. Human football opinions are hypotheses/context, not model
  truth. Disagree clearly when evidence conflicts with a preferred narrative.
- Keep the engine independent of ownership bias and human intuition. An original
  investment thesis supplies decision context but does not protect an owned
  player from being sold.
- Human overrides must be explicit and evaluable, pass the same trusted legality
  boundary, and never silently rewrite model outputs.
- Engine = authority; agents/LLMs = assistants. LLMs may explain validated
  decisions, not construct squad legality, transfers, XI, bench, captain or vice.
- Improve decisions rather than optimize for engagement.
- xFP predicts points; the decision engine decides actions under its explicit
  objective and constraints; the human owns the final FPL action.
- Judge a decision using information available before the deadline, not solely
  its realized outcome. A good process can lose in a noisy game; a good outcome
  cannot retroactively legitimize leaked information.
- Transfer opportunity cost and future flexibility matter. This is a product
  principle, not a claim that Engine v1 values future transfers. Today it only
  prefers ROLL on an objective tie and otherwise optimizes a single GW.
- Every layer must earn the right to influence the layer above it. If a rule
  matters, enforce it architecturally rather than depending on an LLM reminder.

- Narrative/football, population, user-behavior and research evidence must not
  silently flow into xFP, optimization or production decision authority. Future
  product-user records are not automatic training data; research use requires
  explicit consent and a privacy architecture.
- Failed experiments remain negative. Only a new preregistered experiment that
  independently earns promotion may introduce an improvement; it does not
  rewrite the old result.
- UX direction remains deliberately undecided.

## Known minutes limitation (human-confirmed historical context)

xFP v0.1 can produce mechanically valid but football-questionable minutes,
including zero expected minutes for an officially available player after a
change of club/context. This is a known model limitation, not a current claim
about any particular player. The repository's previous-gameweek minutes logic
is in [predictions.py](../../src/fpl_decision_engine/predictions.py).

Humans, the application, frontend and LLM must not silently repair or replace
that projection. Improvements must earn production influence through the
experiment/model-promotion process. Changing football facts must be freshly
verified rather than permanently encoded as project truth.

## Repository-grounded constraints

The [README](../../README.md) distinguishes the model's limited scoring scope
from the optimizer's correctness. xFP v0.1 predicts only appearance, goal and
assist components. It omits clean sheets, goalkeeper saves, goals-conceded
deductions, bonus, cards and defensive-contribution scoring. A legal optimum
under these projections is not a definitive best FPL action, especially for
goalkeepers and defenders. The current objective is starter projections plus
one extra captain copy; it neither simulates substitutions nor values
vice-captain fallback.

Reliability exposes sample size, rate extremity, missingness and diagnostic
sensitivity without changing the official result. A `low_sample` flag is not a
calibrated confidence probability, and universal flags cannot distinguish
players. Do not hide model limitations behind precise objective decimals.

Separate recommendation, human override and outcome. The
[journal](../../src/fpl_decision_engine/decision_journal.py) enforces prospective
server-clock records and explicit historical backfill. The
[diff](../../src/fpl_decision_engine/decision_diff.py) reports what changed, not
why it changed. Neither authorizes an invented counterfactual.

Consistent with the policy above, the proposed
[RFC](../rfcs/0026a-web-product-architecture.md) describes a separate,
explicit-consent research plane and preregistered,
validated, reviewed promotion through a new model/version. These future services
are not implemented just because their boundary is designed.

Experiment failure is useful knowledge, not a reason to keep trying parameter
values until a preferred result passes. Preserve negative outcomes and sealed
holdouts. Distinguish evidence against tested candidates from evidence against
an entire modelling idea.

## UX remains open

The current explicit-ID lookup shell proves a boundary, not the product's
information architecture or visual style. Football Manager-style, decision-first,
squad-first, analyst workspace, consumer app, gameweek narrative,
control-room/dashboard, mobile-first and other approaches remain candidates.
None is selected by this continuity pack. Future UX must preserve canonical
decision authority and useful blocked/error states, whatever its appearance.
