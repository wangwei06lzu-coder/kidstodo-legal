# bench/baselines.py
# -*- coding: utf-8 -*-
"""Real (non-surrogate) Table-1 baseline methods for EMERGENCY-PLAN-1K.

Every method here produces a *committed* `Plan` that is then scored by the
SHARED, real evaluator (`metrics.quality` / `metrics.feasible`) against the real
CBC oracle (`oracle.solve_oracle`). There is **no per-method noise profile and no
calibration** — unlike the deterministic surrogate in
`AI-agents/data/generate_phase4_aegis.py`, which fed one MILP a hand-authored
noise tuple per method and hard-coded the token/iter/HE columns. Whatever Q / FR
/ tokens these protocols produce is what gets reported.

Two families:

  * Deterministic (no LLM, no API key):
      - MILP-Oracle   : full-information optimum  (oracle.solve_oracle)  -> Q=1 ceiling
      - MILP-Partial  : single-pass public optimum (oracle.solve_partial) -> high Q, low FR
      - Fixed-Rule    : ship-from-stock then produce remainder at nominal yield on the
                        first allowed line (a real heuristic planner)

  * LLM-driven (real calls through the project's own client,
    `agents_llm.llm_agent_base.LLMAgentBase` — so the OpenAI/Ollama/Hitachi switch
    is exactly the user's existing `OPENAI_*` env):
      - GPT-Single    : one-shot direct plan (no refinement)
      - Delphi-Voting : N independent experts -> per-quantity median consensus
      - MetaGPT       : SOP roles in sequence (planner -> QA fix)
      - OptiMUS       : LLM models the problem, then a public-feasibility "solve"
      - Code-Gen      : LLM emits the computed plan, then a public-feasibility "solve"

  * External frameworks (the REAL installed system, adapted to this SAME shared
    env — same input, same metered `env.query` door, same scoring — gated on
    availability so the bench still runs without them; see bench/FRAMEWORKS_INSTALL.md):
      - AutoGen       : real `autogen-agentchat` group chat; `env.query` exposed
                        to its agents as tools. In-process. baselines_autogen.py.
      - MetaGPT-Real  : real `metagpt` SOP via a SUBPROCESS bridge to its own
                        Python-3.9-3.11 venv (it pins deps incompatible with the
                        bench's 3.14). baselines_metagpt.py + _metagpt_runner.py.
      - OptiMUS-Real  : real OptiMUS, GATED behind a Gurobi licence + an explicit
                        LLM-generated-code-execution opt-in (it has no CBC backend
                        and runs generated code); omitted until provided.
                        baselines_optimus.py.
    The `MetaGPT` / `OptiMUS` rows in LLM_METHODS remain LABELLED protocol
    imitations — the public-only stand-ins used when the real framework is absent.

  * AEGIS ablations (E-3 ablation table — isolate one AEGIS component each, all
    DETERMINISTIC except the LLM one; every column starts from the SAME env and
    is scored by the SAME TRUE-param evaluator):
      - AEGIS-NoRoute     : ablate the typed gap-router (Steps 3-9). Queries only
                            the directly-named entity's own specialist and SKIPS
                            the dependent-SKU -> QualityAgent routing that finds the
                            hidden (correlated) yield gap -> resolves the obvious
                            disruption but misses the primary gap.
      - AEGIS-StaticPrio  : ablate the MILP optimisation (Step 11). Does the FULL
                            routing (so it knows every truth) but allocates with a
                            static first-allowed-line heuristic instead of the
                            optimal re-solve -> fixes the yield error Fixed-Rule
                            misses, yet can still break capacity feasibility.
      - AEGIS-SinglePassLLM : ablate the refinement loop. Full routing with the
                            resolved truths injected into ONE LLM planning call and
                            NO critique/refine round -> isolates what the iterative
                            plan-review loop adds on top of coordination evidence.
    The full method (COORDINATED.AEGIS-Coord) is the reference column: routing +
    optimal re-solve. Each ablation removes exactly one of those pieces, sharing
    the SAME router (`_route_gaps`) and shadow (`_resolved_scenario`) so the only
    difference between a column and the reference is the ablated component.

The LLM methods see ONLY the PUBLIC view of a scenario (nominal yield, full line
availability, nominal material lead time). That is the whole point: a plan that
trusts nominal parameters over-commits against the hidden yield/availability/lead
hit and fails FR under TRUE params — the gap AEGIS is meant to close.

Robustness: a malformed / failed LLM response degrades to `Plan.do_nothing`
(scored Q=0) rather than raising, so a long real run never dies on one bad reply.

`make sure the code works`: deterministic methods + the parser are covered by
`bench/tests/test_baselines.py` with a mock LLM (no network); the live LLM path
is smoke-checked against local Ollama, and switches to OpenAI by env only.
"""
from __future__ import annotations

import dataclasses
import json
import os
from statistics import median
from typing import Callable, Dict, List, Optional, Tuple

from .config import BENCH, BenchConfig
from .environment import (AGENT_EQUIPMENT, AGENT_QUALITY, AGENT_SUPPLY,
                          BenchEnvironment, build_environment)
from .milp_model import Plan, build_and_solve
from .oracle import solve_oracle, solve_partial
from .scenario import Scenario

# Real external frameworks (optional). The module imports cleanly even when the
# framework is absent; `autogen_available()` gates whether the method is offered.
try:
    from .baselines_autogen import autogen_available, run_autogen
except Exception:  # pragma: no cover - defensive
    def autogen_available() -> bool:  # type: ignore
        return False
    run_autogen = None  # type: ignore

# Real MetaGPT (separate venv, subprocess bridge) and real OptiMUS (Gurobi-licensed,
# code-exec opt-in) — same gated pattern as AutoGen; omitted when unavailable.
try:
    from .baselines_metagpt import metagpt_available, run_metagpt
except Exception:  # pragma: no cover - defensive
    def metagpt_available() -> bool:  # type: ignore
        return False
    run_metagpt = None  # type: ignore
try:
    from .baselines_optimus import optimus_available, run_optimus
except Exception:  # pragma: no cover - defensive
    def optimus_available() -> bool:  # type: ignore
        return False
    run_optimus = None  # type: ignore

PlanSpec = Dict[str, object]


# ---------------------------------------------------------------------------
# Public view + plan parsing (shared by every LLM method)
# ---------------------------------------------------------------------------
def public_view(scn: Scenario) -> Dict[str, object]:
    """The PUBLIC-only projection an external planner is allowed to see.

    The canonical projection now lives in `environment.BenchEnvironment.public_input()`
    so there is exactly ONE definition of "the same input every method starts from".
    This thin shim is kept for any external caller that only has a `Scenario`.
    """
    return build_environment(scn).public_input()


def _extract_plan_rows(spec: object) -> Optional[list]:
    """Find the list of per-SKU plan rows in a (possibly noisy) model reply.

    Accepts the canonical {"plan":[...]}, a bare top-level list, or — leniently —
    the first list-valued field whose items look like SKU rows (real models
    occasionally name the key differently). Anything else -> None (do-nothing).
    """
    if isinstance(spec, list):
        return spec
    if isinstance(spec, dict):
        rows = spec.get("plan")
        if isinstance(rows, list):
            return rows
        for v in spec.values():
            if isinstance(v, list) and any(isinstance(x, dict) and "sku" in x for x in v):
                return v
    return None


def plan_from_spec(spec: object, scn: Scenario) -> Plan:
    """Map an LLM/heuristic plan spec into a scored-ready `Plan`.

    Expected spec shape (robust to noise / missing keys):
        {"plan": [{"sku": "<id>", "ship_from_stock": <num>,
                   "produce": [{"line": "<id>", "units": <num>}, ...]}, ...]}

    Unknown SKUs are ignored; production on non-allowed lines is dropped; ship is
    clamped to [0, finished_stock]; negatives are clamped to 0. A spec with no
    usable plan list (see `_extract_plan_rows`) degrades to do-nothing.
    """
    rows = _extract_plan_rows(spec)
    if rows is None:
        return Plan.do_nothing(scn)

    by_id = {s.sku_id: s for s in scn.skus}
    allowed = {s.sku_id: set(s.allowed_lines) for s in scn.skus}

    ship: Dict[str, float] = {s.sku_id: 0.0 for s in scn.skus}
    prod_sl: Dict[Tuple[str, str], float] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = row.get("sku")
        if sid not in by_id:
            continue
        sk = by_id[sid]
        sh = _num(row.get("ship_from_stock"))
        ship[sid] = max(0.0, min(sh, sk.finished_stock))
        produce = row.get("produce")
        if isinstance(produce, list):
            for p in produce:
                if not isinstance(p, dict):
                    continue
                lid = p.get("line")
                if lid not in allowed[sid]:
                    continue
                units = max(0.0, _num(p.get("units")))
                if units > 0:
                    prod_sl[(sid, lid)] = prod_sl.get((sid, lid), 0.0) + units

    return _assemble(ship, prod_sl, scn, status="baseline")


# ---------------------------------------------------------------------------
# Deterministic methods (no LLM)
# ---------------------------------------------------------------------------
def fixed_rule(scn: Scenario, cfg: BenchConfig = BENCH) -> Plan:
    """A real rule-based planner: ship from stock, then produce the remainder at
    NOMINAL yield on the first allowed line. Plans exactly to demand on public
    info -> typically infeasible under a hidden yield hit (the intended weakness).
    """
    ship: Dict[str, float] = {}
    prod_sl: Dict[Tuple[str, str], float] = {}
    for s in scn.skus:
        sh = min(s.demand, s.finished_stock)
        ship[s.sku_id] = sh
        remaining = max(0.0, s.demand - sh)
        if remaining > 0 and s.allowed_lines:
            need = remaining / max(s.nominal_yield, 1e-6)
            prod_sl[(s.sku_id, s.allowed_lines[0])] = need
    return _assemble(ship, prod_sl, scn, status="fixed_rule")


def repair_public(plan: Plan, scn: Scenario, cfg: BenchConfig = BENCH) -> Plan:
    """Project a plan onto PUBLIC feasibility (the 'solver' step for the
    MILP-grounded baselines): clamp each line to its public capacity and each
    non-replenishable material to on-hand, scaling proportionally."""
    prod_sl: Dict[Tuple[str, str], float] = dict(plan.prod_sl)

    for l in scn.lines:
        cap = l.capacity_per_day * scn.horizon_days * l.nominal_avail
        keys = [(s.sku_id, l.line_id) for s in scn.skus if (s.sku_id, l.line_id) in prod_sl]
        used = sum(prod_sl[k] for k in keys)
        if used > cap and used > 0:
            f = cap / used
            for k in keys:
                prod_sl[k] *= f

    def prod_of(sid: str) -> float:
        return sum(prod_sl.get((sid, l.line_id), 0.0) for l in scn.lines)

    for m in scn.materials:
        if m.nominal_lead_time <= scn.horizon_days:
            continue  # replenishable -> unconstrained on public info
        used = sum(s.bom.get(m.material_id, 0.0) * prod_of(s.sku_id) for s in scn.skus)
        if used > m.onhand and used > 0:
            f = m.onhand / used
            for k in list(prod_sl):
                prod_sl[k] *= f

    return _assemble(dict(plan.ship), prod_sl, scn, status="repaired")


# ---------------------------------------------------------------------------
# LLM methods — real protocols over the project's own client
# ---------------------------------------------------------------------------
_SYS = (
    "You are {role}. You produce a feasible emergency production/shipping plan for a "
    "5-day horizon. You see only PUBLIC parameters. Good units of a SKU = "
    "nominal_yield * produced units. Meet each SKU's demand using finished_stock plus "
    "production on its allowed lines, without exceeding any line's capacity_units_total "
    "or any non-replenishable material's on-hand. Respond with STRICT JSON only:\n"
    '{"plan":[{"sku":"<id>","ship_from_stock":<number>,'
    '"produce":[{"line":"<id>","units":<number>}]}]}'
)


def _plan_prompt(env: BenchEnvironment, role: str) -> Tuple[str, str]:
    sysd = _SYS.replace("{role}", role)   # not .format: _SYS embeds literal JSON braces
    usr = "SCENARIO (public view):\n" + json.dumps(env.public_input(), ensure_ascii=False)
    return sysd, usr


def _critique_prompt(env: BenchEnvironment, plan: Plan, role: str) -> Tuple[str, str]:
    scn = env.scenario
    sysd = _SYS.replace("{role}", role)   # not .format: _SYS embeds literal JSON braces
    cur = {
        "plan": [
            {
                "sku": s.sku_id,
                "ship_from_stock": round(plan.ship.get(s.sku_id, 0.0), 2),
                "produce": [
                    {"line": l.line_id, "units": round(plan.prod_sl[(s.sku_id, l.line_id)], 2)}
                    for l in scn.lines
                    if (s.sku_id, l.line_id) in plan.prod_sl
                ],
            }
            for s in scn.skus
        ]
    }
    usr = (
        "SCENARIO (public view):\n" + json.dumps(env.public_input(), ensure_ascii=False)
        + "\n\nCURRENT PLAN to review and improve:\n" + json.dumps(cur, ensure_ascii=False)
        + "\n\nReturn the improved plan as STRICT JSON in the same schema."
    )
    return sysd, usr


# Every method takes the SAME shared environment, so the input is identical for all.
# plan_from_spec touches only PUBLIC Sku fields (id / finished_stock / allowed_lines);
# the hidden truth is reachable only through env.query (metered).
def gpt_single(env: BenchEnvironment, llm: "LLMProto") -> Tuple[Plan, int]:
    spec = llm.call_json(*_plan_prompt(env, "a single expert production planner"))
    return plan_from_spec(spec, env.scenario), 1


def delphi_voting(env: BenchEnvironment, llm: "LLMProto", n_experts: int = 3) -> Tuple[Plan, int]:
    plans: List[Plan] = []
    for i in range(n_experts):
        spec = llm.call_json(*_plan_prompt(env, f"independent expert #{i + 1} in a Delphi panel"))
        plans.append(plan_from_spec(spec, env.scenario))
    return _median_plan(plans, env.scenario), 1


def metagpt(env: BenchEnvironment, llm: "LLMProto") -> Tuple[Plan, int]:
    # SOP: a planner role drafts, a QA role fixes capacity/feasibility.
    scn = env.scenario
    draft = plan_from_spec(
        llm.call_json(*_plan_prompt(env, "the Planner role in an SOP multi-agent team")), scn
    )
    qa_sys, qa_usr = _critique_prompt(env, draft, role="the QA-Engineer role; fix any infeasibility")
    fixed = plan_from_spec(llm.call_json(qa_sys, qa_usr), scn)
    final = fixed if _nonempty(fixed) else draft
    return final, 2


def autogen(env: BenchEnvironment, llm: "LLMProto", turns: int = 2) -> Tuple[Plan, int]:
    # Group-chat style: planner proposes, critic revises, repeat.
    scn = env.scenario
    plan = plan_from_spec(llm.call_json(*_plan_prompt(env, "the Planner agent in a group chat")), scn)
    for _ in range(max(0, turns - 1)):
        c_sys, c_usr = _critique_prompt(env, plan, role="the Critic agent; improve feasibility and coverage")
        revised = plan_from_spec(llm.call_json(c_sys, c_usr), scn)
        if _nonempty(revised):
            plan = revised
    return plan, turns


def optimus(env: BenchEnvironment, llm: "LLMProto") -> Tuple[Plan, int]:
    spec = llm.call_json(*_plan_prompt(env, "an OptiMUS-style optimization modeler"))
    return repair_public(plan_from_spec(spec, env.scenario), env.scenario), 1


def code_gen(env: BenchEnvironment, llm: "LLMProto") -> Tuple[Plan, int]:
    spec = llm.call_json(*_plan_prompt(env, "a code-generation agent emitting the computed optimal plan"))
    return repair_public(plan_from_spec(spec, env.scenario), env.scenario), 1


# ---------------------------------------------------------------------------
# AEGIS — our coordinated method (typed gap-routing over the SAME agents)
# ---------------------------------------------------------------------------
def _resolved_scenario(scn: Scenario, q_yield: Dict[str, float],
                       q_avail: Dict[str, float], q_lead: Dict[str, int]) -> Scenario:
    """A shadow scenario whose PUBLIC fields are overwritten by the truths AEGIS
    actually paid to learn (via env.query). Solving this under 'public' therefore
    reflects the resolved gaps; entities AEGIS did not query stay at nominal. The
    shadow is used ONLY to solve — scoring is always against the original scenario.
    """
    skus = [
        dataclasses.replace(
            s,
            nominal_yield=q_yield.get(s.sku_id, s.nominal_yield),
            true_yield=min(s.true_yield, q_yield.get(s.sku_id, s.true_yield)),
        )
        for s in scn.skus
    ]
    lines = [
        dataclasses.replace(l, nominal_avail=q_avail.get(l.line_id, l.nominal_avail))
        for l in scn.lines
    ]
    materials = [
        dataclasses.replace(m, nominal_lead_time=q_lead.get(m.material_id, m.nominal_lead_time))
        for m in scn.materials
    ]
    return dataclasses.replace(scn, skus=skus, lines=lines, materials=materials)


def _route_gaps(env: BenchEnvironment) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, int]]:
    """The AEGIS typed gap-router (Steps 3-9), shared by AEGIS-Coord and its
    ablations so a column differs from the reference ONLY in the ablated piece.

    From the PUBLIC disruption (kind + affected ids) it (a) queries the affected
    entity's own specialist for the directly-named hit, and (b) expands to the
    dependent SKUs and consults the QualityAgent for the hidden, correlated yield
    gap (the primary gap a single-pass planner misses). Every query is metered on
    `env` — that is the coordination cost. Returns (q_yield, q_avail, q_lead) for
    only the entities it paid to learn; everything else stays nominal downstream.
    """
    scn = env.scenario
    dis = env.public_input()["emergent_disruption"]
    kind = dis["kind"]
    affected = set(dis["affected"])
    sku_ids = {s.sku_id for s in scn.skus}

    q_yield: Dict[str, float] = {}
    q_avail: Dict[str, float] = {}
    q_lead: Dict[str, int] = {}
    dependent: set = set()

    if kind == "EQUIP":
        for lid in affected:
            v = env.query(AGENT_EQUIPMENT, lid)
            if v is not None:
                q_avail[lid] = v
        for s in scn.skus:
            if affected.intersection(s.allowed_lines):
                dependent.add(s.sku_id)
    elif kind == "SUPPLY":
        for mid in affected:
            v = env.query(AGENT_SUPPLY, mid)
            if v is not None:
                q_lead[mid] = int(v)
        for s in scn.skus:
            if affected.intersection(s.bom.keys()):
                dependent.add(s.sku_id)
    elif kind == "MULTI":
        # COMPOUND emergency (several types at once): route each affected entity
        # to its owning specialist BY TYPE, then consult QualityAgent for every
        # dependent SKU below. Same metered door, so cost is paid per gap learned.
        line_ids = {l.line_id for l in scn.lines}
        mat_ids = {m.material_id for m in scn.materials}
        for ent in affected:
            if ent in line_ids:
                v = env.query(AGENT_EQUIPMENT, ent)
                if v is not None:
                    q_avail[ent] = v
                dependent |= {s.sku_id for s in scn.skus if ent in s.allowed_lines}
            elif ent in mat_ids:
                v = env.query(AGENT_SUPPLY, ent)
                if v is not None:
                    q_lead[ent] = int(round(v))
                dependent |= {s.sku_id for s in scn.skus if ent in s.bom}
            elif ent in sku_ids:
                dependent.add(ent)
    else:  # DEMAND: the affected ids are the surged SKUs themselves
        dependent |= (affected & sku_ids)

    # The hidden yield hit lands on the dependent SKUs -> resolve it via QualityAgent.
    for sid in dependent:
        v = env.query(AGENT_QUALITY, sid)
        if v is not None:
            q_yield[sid] = v

    return q_yield, q_avail, q_lead


def aegis_coord(env: BenchEnvironment, llm: Optional["LLMProto"] = None) -> Tuple[Plan, int]:
    """AEGIS-style typed gap-routing under the SAME conditions as every baseline.

    Starts from the identical public input, reads the emergent disruption (kind +
    affected ids — public), routes to the matching specialist for the affected
    entities, and always consults the QualityAgent for the SKUs that depend on them
    (the hidden quality hit is the primary gap). It then RE-SOLVES using only the
    truths it paid to learn — every such fact shows up in env.meter.n_queries
    (its coordination cost). Entities it never queried stay at nominal, so it is
    NOT the oracle: it pays for exactly the evidence it routes to.
    """
    scn = env.scenario
    q_yield, q_avail, q_lead = _route_gaps(env)               # Steps 3-9: typed routing
    shadow = _resolved_scenario(scn, q_yield, q_avail, q_lead)
    plan = build_and_solve(shadow, info="public", cfg=env.cfg)  # Step 11: optimal re-solve
    return plan, 2   # initial public read + one coordinated re-solve


# ---------------------------------------------------------------------------
# AEGIS ablations — remove exactly one component of aegis_coord (E-3 Table-3).
# Each shares _route_gaps / _resolved_scenario with the full method so the only
# delta is the ablated piece; all are scored by the same TRUE-param evaluator.
# ---------------------------------------------------------------------------
def aegis_no_route(env: BenchEnvironment, llm: Optional["LLMProto"] = None) -> Tuple[Plan, int]:
    """ABLATION — no gap-routing (remove Steps 3-9 typed routing).

    A coordinator that lacks the typed gap-router still does the OBVIOUS thing —
    query the specialist that owns the directly-named disruption (the failed line,
    the late material) — but it never expands to the dependent SKUs nor consults
    the QualityAgent, so the hidden correlated yield gap (the PRIMARY gap) goes
    unresolved. For a DEMAND disruption there is no named owner to query, so it
    collapses to a pure single-pass public solve. Isolates the value of routing
    to the hidden gap.
    """
    scn = env.scenario
    dis = env.public_input()["emergent_disruption"]
    kind = dis["kind"]
    affected = set(dis["affected"])

    q_yield: Dict[str, float] = {}
    q_avail: Dict[str, float] = {}
    q_lead: Dict[str, int] = {}
    if kind == "EQUIP":
        for lid in affected:
            v = env.query(AGENT_EQUIPMENT, lid)
            if v is not None:
                q_avail[lid] = v
    elif kind == "SUPPLY":
        for mid in affected:
            v = env.query(AGENT_SUPPLY, mid)
            if v is not None:
                q_lead[mid] = int(v)
    # NO dependent-SKU expansion, NO QualityAgent consult: the hidden yield gap is missed.

    shadow = _resolved_scenario(scn, q_yield, q_avail, q_lead)
    plan = build_and_solve(shadow, info="public", cfg=env.cfg)
    return plan, 1


def aegis_static_prio(env: BenchEnvironment, llm: Optional["LLMProto"] = None) -> Tuple[Plan, int]:
    """ABLATION — static priority instead of MILP optimisation (remove Step 11).

    Does the FULL typed routing (so it learns every truth AEGIS-Coord does, and
    pays the same n_queries), but replaces the optimal re-solve with a static
    first-allowed-line heuristic over the RESOLVED scenario: ship from stock, then
    size production at the now-known TRUE yield on each SKU's first allowed line.
    Because it knows the true yield it fixes the under-production error Fixed-Rule
    makes on nominal info, but the dumb single-line allocation can overrun a line's
    capacity — isolating what the MILP optimisation buys on top of good evidence.
    """
    scn = env.scenario
    q_yield, q_avail, q_lead = _route_gaps(env)               # full routing (Steps 3-9)
    shadow = _resolved_scenario(scn, q_yield, q_avail, q_lead)
    plan = fixed_rule(shadow, env.cfg)                        # static allocation, no MILP
    return dataclasses.replace(plan, status="aegis_static_prio"), 2


_HEDGE = (
    "\n\nIMPORTANT — the SPECIALIST FINDINGS are NOISY measurements (roughly +/-15% "
    "error); each TRUE value may be WORSE than reported (lower yield/availability, "
    "longer lead time). Plan ROBUSTLY: treat every finding as an uncertain estimate "
    "and leave a SAFETY MARGIN so the plan stays feasible even if each true value is "
    "up to ~20% worse than reported. Do NOT commit to the reported numbers at face "
    "value — hedge against the worst case within that band while staying within every "
    "line's capacity and every material's on-hand."
)


def _coordinated_plan_prompt(env: BenchEnvironment, q_yield: Dict[str, float],
                             q_avail: Dict[str, float], q_lead: Dict[str, int],
                             hedge: bool = False) -> Tuple[str, str]:
    """The single-pass coordinated prompt: public view PLUS the resolved truths the
    router paid to learn, with an explicit instruction to plan against THOSE. With
    ``hedge=True`` it additionally instructs the model to treat the findings as noisy
    and leave safety margins (the 'just prompt it to be robust' ablation)."""
    sysd = _SYS.replace("{role}", "the AEGIS coordinator acting on specialist findings")
    findings = {
        "true_yield_by_sku": {k: round(float(v), 4) for k, v in q_yield.items()},
        "true_avail_by_line": {k: round(float(v), 4) for k, v in q_avail.items()},
        "true_lead_time_by_material": {k: int(v) for k, v in q_lead.items()},
    }
    usr = (
        "SCENARIO (public view):\n" + json.dumps(env.public_input(), ensure_ascii=False)
        + "\n\nSPECIALIST FINDINGS (hidden truths resolved by coordination — plan "
          "against THESE, not the nominal values):\n" + json.dumps(findings, ensure_ascii=False)
        + (_HEDGE if hedge else "")
        + "\n\nReturn the plan as STRICT JSON. Single pass — there is no second round."
    )
    return sysd, usr


def aegis_single_pass_llm(env: BenchEnvironment, llm: "LLMProto") -> Tuple[Plan, int]:
    """ABLATION — single-pass LLM, no refinement loop.

    Does the FULL typed routing (same evidence + n_queries as AEGIS-Coord), injects
    the resolved truths into ONE LLM planning call, and commits the result with NO
    critique/refine iteration. Isolates what the iterative plan-review loop adds on
    top of the coordination evidence. A malformed reply degrades to do-nothing.
    """
    scn = env.scenario
    q_yield, q_avail, q_lead = _route_gaps(env)               # full routing (Steps 3-9)
    spec = llm.call_json(*_coordinated_plan_prompt(env, q_yield, q_avail, q_lead))
    return plan_from_spec(spec, scn), 1


def aegis_single_pass_llm_hedge(env: BenchEnvironment, llm: "LLMProto") -> Tuple[Plan, int]:
    """ABLATION — 'just prompt it to be robust'. Same full routing + single LLM call as
    AEGIS-SinglePassLLM, but the prompt EXPLICITLY tells the model the findings are noisy
    and to leave safety margins — the robustness AEGIS otherwise provides via the
    deterministic interval compile (Leg C). Tests the obvious reviewer rebuttal "did you
    just try asking the LLM to hedge?". The honest expectation: prompting does NOT close
    the coupling collapse, because the model cannot reliably compute the right per-
    constraint margin across k coupled gaps (it under-hedges and stays infeasible, or
    over-hedges and busts capacity). A malformed reply degrades to do-nothing."""
    scn = env.scenario
    q_yield, q_avail, q_lead = _route_gaps(env)               # same routing as single-pass
    spec = llm.call_json(*_coordinated_plan_prompt(env, q_yield, q_avail, q_lead, hedge=True))
    return plan_from_spec(spec, scn), 1


# ---------------------------------------------------------------------------
# Registries (name -> callable). Each callable: (env, llm) -> (Plan, iterations)
# ---------------------------------------------------------------------------
DETERMINISTIC: Dict[str, Callable[[BenchEnvironment, Optional["LLMProto"]], Tuple[Plan, int]]] = {
    "MILP-Oracle": lambda env, llm: (solve_oracle(env.scenario, env.cfg), 0),
    "MILP-Partial": lambda env, llm: (solve_partial(env.scenario, env.cfg), 0),
    "Fixed-Rule": lambda env, llm: (fixed_rule(env.scenario, env.cfg), 0),
}

# Our method. Deterministic gap-router here (no LLM needed); the live 18-step
# coordinator is the separate D6 bridge. Either way it runs on the SAME env.
COORDINATED: Dict[str, Callable[[BenchEnvironment, Optional["LLMProto"]], Tuple[Plan, int]]] = {
    "AEGIS-Coord": aegis_coord,
}

# AEGIS-RVoIE — the robust value-of-information method (paper v2 §4). The
# contribution code lives in the rvoie/ package (rooting rule: coordinator_scenario
# is the main project; aegis is a frozen donor). Registered when importable so a
# sweep includes it where the rvoie deps (pulp/pydantic) are present, and silently
# omits it otherwise — same pattern as the external frameworks below.
try:
    from rvoie.bench_method import aegis_rvoie as _aegis_rvoie  # noqa: E402
    COORDINATED["AEGIS-RVoIE"] = _aegis_rvoie
except Exception:  # pragma: no cover - rvoie optional at import time
    pass

# AEGIS-Iter — the FULL iterative RVoIE (Leg A complete detection + Leg B VoI
# routing + Leg C robust compile, looped with re-detection; paper v2 §4.6 Alg 1).
# Differs from AEGIS-RVoIE (single-pass) by the re-detect loop that recovers
# cascaded, coupling-DEPTH>0 gaps — the mechanism behind the Thm 2 separation.
try:
    from rvoie.run import aegis_iter as _aegis_iter  # noqa: E402
    COORDINATED["AEGIS-Iter"] = _aegis_iter
except Exception:  # pragma: no cover - rvoie optional at import time
    pass

# RVoIE leg ablations (paper v2 §6.9 / EX-ABL): −LegA (free-form detect) and −LegB
# (random route). Kept in their OWN registry so the E-3 ablation set (ABLATIONS)
# and the COORDINATED-membership tests stay unchanged; the EX-ABL driver reads them.
RVOIE_ABLATIONS: Dict[str, Callable[["BenchEnvironment", Optional["LLMProto"]], Tuple[Plan, int]]] = {}
try:
    from rvoie.ablations import (aegis_no_lega as _aegis_no_lega,  # noqa: E402
                                 aegis_no_legb as _aegis_no_legb,
                                 aegis_no_legc as _aegis_no_legc)
    RVOIE_ABLATIONS["AEGIS-NoLegA"] = _aegis_no_lega
    RVOIE_ABLATIONS["AEGIS-NoLegB"] = _aegis_no_legb
    RVOIE_ABLATIONS["AEGIS-NoLegC"] = _aegis_no_legc
except Exception:  # pragma: no cover - rvoie optional at import time
    pass

LLM_METHODS: Dict[str, Callable[[BenchEnvironment, "LLMProto"], Tuple[Plan, int]]] = {
    "GPT-Single": gpt_single,
    "Delphi-Voting": delphi_voting,
    "MetaGPT": metagpt,
    "OptiMUS": optimus,
    "Code-Gen": code_gen,
    # 'just prompt it to be robust' ablation — single-pass + explicit hedging prompt.
    "AEGIS-SinglePassLLM-Hedge": aegis_single_pass_llm_hedge,
}

# Tool-using LLM agent — the FAIR control: an LLM given the SAME metered query door
# AEGIS uses (env.query), driven as a ReAct consult->commit loop. Unlike GPT-Single /
# MetaGPT / AutoGen (public statement only, n_queries=0), this CAN reach the private
# facts; whether it recovers a depth>0 cascade without schema-grounded re-detection is
# the honest question (rvoie/.. is not in its prompt). See bench/baselines_toolagent.py.
try:
    from .baselines_toolagent import run_tool_agent as _run_tool_agent  # noqa: E402

    def _tool_agent(env: BenchEnvironment, llm: "LLMProto") -> Tuple[Plan, int]:
        return _run_tool_agent(env, llm, max_rounds=4, verify=False)

    def _tool_agent_iter(env: BenchEnvironment, llm: "LLMProto") -> Tuple[Plan, int]:
        return _run_tool_agent(env, llm, max_rounds=6, verify=True)

    LLM_METHODS["LLM-ToolAgent"] = _tool_agent
    LLM_METHODS["LLM-ToolAgent-Iter"] = _tool_agent_iter
except Exception:  # pragma: no cover - defensive; module has no optional deps
    pass

# AEGIS ablation columns (E-3 Table-3). All take (env, llm); the two deterministic
# ones ignore llm, AEGIS-SinglePassLLM requires it (listed in ABLATION_NEEDS_LLM so
# the driver builds a client and meters its tokens just like an LLM baseline).
ABLATIONS: Dict[str, Callable[[BenchEnvironment, Optional["LLMProto"]], Tuple[Plan, int]]] = {
    "AEGIS-NoRoute": aegis_no_route,
    "AEGIS-StaticPrio": aegis_static_prio,
    "AEGIS-SinglePassLLM": aegis_single_pass_llm,
}
ABLATION_NEEDS_LLM = frozenset({"AEGIS-SinglePassLLM"})

# External frameworks: the REAL installed system, adapted to the shared env.
# Each returns (Plan, ci, tokens) — tokens come from the framework's OWN client,
# so the driver routes these specially. Registered only when importable, so a
# sweep `all` includes them where present and silently omits them otherwise.
FRAMEWORK_METHODS: Dict[str, Callable[[BenchEnvironment], Tuple[Plan, int, int]]] = {}
if autogen_available():
    def _run_autogen_method(env: BenchEnvironment) -> Tuple[Plan, int, int]:
        """Real AutoGen, reading the SAME OPENAI_* env every LLM method uses."""
        return run_autogen(
            env,
            base_url=os.environ.get("OPENAI_BASE_URL"),
            model=os.environ.get("OPENAI_MODEL"),
            api_key=os.environ.get("OPENAI_API_KEY"),
            max_rounds=int(os.environ.get("AUTOGEN_MAX_ROUNDS", "6")),
            max_tokens=os.environ.get("BASE_MAX_TOKENS"),
        )
    FRAMEWORK_METHODS["AutoGen"] = _run_autogen_method

if metagpt_available():
    def _run_metagpt_method(env: BenchEnvironment) -> Tuple[Plan, int, int]:
        """Real MetaGPT SOP via its own venv (subprocess); reads OPENAI_* env."""
        return run_metagpt(
            env,
            base_url=os.environ.get("OPENAI_BASE_URL"),
            model=os.environ.get("OPENAI_MODEL"),
            max_tokens=os.environ.get("BASE_MAX_TOKENS"),
        )
    FRAMEWORK_METHODS["MetaGPT-Real"] = _run_metagpt_method

if optimus_available():
    FRAMEWORK_METHODS["OptiMUS-Real"] = run_optimus

# Ablations sit right after COORDINATED so ALL_METHODS[:4] (the Table-1 reference
# prefix MILP-Oracle/MILP-Partial/Fixed-Rule/AEGIS-Coord) is unchanged.
ALL_METHODS = (list(DETERMINISTIC) + list(COORDINATED) + list(ABLATIONS)
               + list(RVOIE_ABLATIONS) + list(LLM_METHODS) + list(FRAMEWORK_METHODS))


def run_method(name: str, env: BenchEnvironment, llm: Optional["LLMProto"]) -> Tuple[Plan, int]:
    """Dispatch a method by name on a shared environment. Deterministic, coordinated
    and the two deterministic ablations ignore `llm`; LLM methods and
    AEGIS-SinglePassLLM require it."""
    if name in DETERMINISTIC:
        return DETERMINISTIC[name](env, llm)
    if name in COORDINATED:
        return COORDINATED[name](env, llm)
    if name in ABLATIONS:
        if name in ABLATION_NEEDS_LLM and llm is None:
            raise ValueError(f"method {name!r} needs an LLM client (got None)")
        return ABLATIONS[name](env, llm)
    if name in RVOIE_ABLATIONS:
        return RVOIE_ABLATIONS[name](env, llm)
    if name in LLM_METHODS:
        if llm is None:
            raise ValueError(f"method {name!r} needs an LLM client (got None)")
        return LLM_METHODS[name](env, llm)
    if name in FRAMEWORK_METHODS:
        plan, ci, _tokens = FRAMEWORK_METHODS[name](env)   # tokens read by the driver
        return plan, ci
    raise KeyError(f"unknown method {name!r}; known: {ALL_METHODS}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _num(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _assemble(ship: Dict[str, float], prod_sl: Dict[Tuple[str, str], float],
              scn: Scenario, status: str) -> Plan:
    prod = {
        s.sku_id: sum(prod_sl.get((s.sku_id, l.line_id), 0.0) for l in scn.lines)
        for s in scn.skus
    }
    ship_full = {s.sku_id: float(ship.get(s.sku_id, 0.0)) for s in scn.skus}
    return Plan(ship=ship_full, prod=prod, prod_sl=dict(prod_sl),
                late={}, objective=0.0, status=status)


def _median_plan(plans: List[Plan], scn: Scenario) -> Plan:
    if not plans:
        return Plan.do_nothing(scn)
    ship = {s.sku_id: float(median([p.ship.get(s.sku_id, 0.0) for p in plans])) for s in scn.skus}
    keys = set()
    for p in plans:
        keys |= set(p.prod_sl)
    prod_sl = {k: float(median([p.prod_sl.get(k, 0.0) for p in plans])) for k in keys}
    return _assemble(ship, prod_sl, scn, status="delphi_median")


def _nonempty(plan: Plan) -> bool:
    return any(v > 0 for v in plan.ship.values()) or any(v > 0 for v in plan.prod_sl.values())


# Structural type for the LLM client: anything with these two methods works
# (the real BaselineLLM in run_baselines.py, or a mock in tests).
class LLMProto:  # pragma: no cover - typing aid only
    def call_json(self, system_prompt: str, user_prompt: str) -> Dict[str, object]:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError
