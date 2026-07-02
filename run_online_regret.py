# run_online_regret.py
# -*- coding: utf-8 -*-
"""One-click REGRET comparison for a HOSTED model (paper §6.9 — the honest LLM win).

A regime-appropriate hedging prompt lets an LLM ~match AEGIS on FEASIBILITY, so the
right axis is REGRET (cost vs the oracle), where AEGIS wins on two grounds:
  1. even when feasible, a blanket hedge OVER-PRODUCES -> higher cost-regret than
     AEGIS's re-queried / interval margin;
  2. the LLM's residual infeasibility carries the lateness cliff -> far higher
     EXPECTED regret (AEGIS never misses: deterministic, provably sound, $0).

This runs, on the compound family at sigma=0.15:
  LLM  : AEGIS-SinglePassLLM-Hedge ('just prompt it to hedge'),
         AEGIS-SinglePassLLM       (face-value, no hedge)
  det  : AEGIS-Iter, AEGIS-RVoIE   (the references; $0, no LLM)
and reports per method: FR [Wilson CI], feasible-only regret, expected regret.

  Output: results/ex_regret_online_<model>.json   (checkpointed after each k)
          results/figures/fig_regret_<model>.png

-----------------------------------------------------------------------------
USE (Windows, company key) — identical .env to run_online_llm_all_steps.py:
    python run_online_regret.py gpt-5.1
    python run_online_regret.py claude-opus-4.8
(Or edit MODEL_NAME below and run with no argument.)
Single-pass (not multi-turn), so cheap. Send me results\\ex_regret_online_<model>.json.
-----------------------------------------------------------------------------
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from statistics import mean

# ======================= EDIT ME (the one knob you change) ===================
MODEL_NAME = "gpt-4o"          # e.g. "gpt-5.1", "claude-opus-4.8" — or pass as argv[1]
KS = "1,2,4"                 # coupling levels (regret story lives at k=2,4)
SEEDS = 30                   # scenarios per cell
SIGMA = 0.15
# NOTE: reasoning-model endpoints (gpt-5.x / o-series) spend completion tokens on
# hidden reasoning; the HEDGE prompt is longer, so a low budget truncates it to empty
# -> do-nothing. Keep this generous; the runner also warns if a method never becomes
# feasible in a cell (the do-nothing signature).
BASE_MAX_TOKENS = 16000
MAKE_FIGURE = True
# Env overrides (tune without editing): REGRET_KS / REGRET_SEEDS
KS = os.environ.get("REGRET_KS", KS)
SEEDS = int(os.environ.get("REGRET_SEEDS", SEEDS))
# =============================================================================

LLM_METHODS = ["AEGIS-SinglePassLLM-Hedge", "AEGIS-SinglePassLLM"]
DET_METHODS = ["AEGIS-Iter", "AEGIS-RVoIE"]

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        print("[warn] python-dotenv not installed; relying on the system environment.")
        return
    p = ROOT / ".env"
    if p.exists():
        load_dotenv(p); print(f"[env] loaded {p}")
    else:
        print(f"[env] no .env at {p}; relying on the system environment.")


def _install_newer_model_compat() -> None:
    """gpt-5.x / o-series reject (temperature, max_tokens); retry with
    max_completion_tokens only on a param error. gpt-4o path unchanged."""
    import agents_llm.llm_agent_base as lab
    Base = lab.LLMAgentBase
    if getattr(Base, "_online_compat_installed", False):
        return
    _ERR = ("max_tokens", "max_completion_tokens", "temperature",
            "unsupported", "not supported", "parameter", "invalid_request")

    def call_llm_json(self, system_prompt: str, user_prompt: str):
        msgs = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}]
        legacy = {"temperature": self.temperature, "max_tokens": self.max_tokens}
        newer = {"max_completion_tokens": self.max_tokens}
        order = [newer, legacy] if getattr(self, "_prefer_newer", False) else [legacy, newer]
        last_err = None
        for _ in range(self.retries + 1):
            for kw in order:
                try:
                    resp = self.client.chat.completions.create(
                        model=self.model, messages=msgs, **kw)
                    txt = (resp.choices[0].message.content or "").strip()
                    data = self._safe_json_extract(txt)
                    if not isinstance(data, dict):
                        data = {"raw": txt}
                    self._prefer_newer = ("max_completion_tokens" in kw)
                    return data
                except Exception as e:                          # noqa: BLE001
                    last_err = e
                    if not any(t in str(e).lower() for t in _ERR):
                        break
            time.sleep(0.5)
        return {"error": f"LLM call failed: {last_err}"}

    Base.call_llm_json = call_llm_json
    Base._online_compat_installed = True


def _preflight() -> "object":
    print("\n[preflight] verifying connectivity / key / model / params ...", flush=True)
    try:
        from bench.run_baselines import BaselineLLM
        llm = BaselineLLM()
    except Exception as e:                                       # noqa: BLE001
        sys.exit(f"[preflight] FAILED to build the LLM client: {type(e).__name__}: {e}\n"
                 "  -> check .env: OPENAI_API_TYPE and the matching key/endpoint vars.")
    api = os.environ.get("OPENAI_API_TYPE", "openai").lower()
    base = os.environ.get("OPENAI_BASE_URL", "<default: Hitachi v1 endpoint>")
    print(f"[preflight] backend={api}  model={llm.model}  endpoint={base}")
    t0 = time.time()
    res = llm.call_json("Reply with strict JSON only.",
                        'Return exactly {"ok": true} and nothing else.')
    dt = time.time() - t0
    if not isinstance(res, dict) or "error" in res:
        sys.exit(f"[preflight] LLM call FAILED ({dt:.1f}s): {res}\n"
                 "  Fix .env / MODEL_NAME and re-run — no scenarios were spent.")
    print(f"[preflight] OK ({dt:.1f}s)  sample={res}\n", flush=True)
    return llm


def _run(model: str, out_json: Path) -> dict:
    from bench import baselines as B
    from bench.config import BENCH as cfg
    from bench.environment import NoiseModel, build_environment
    from bench.metrics import feasible, regret
    from bench.milp_model import Plan
    from bench.oracle import solve_oracle
    from bench.scenario_gen import make_compound_scenario
    from bench.stats import fr_cell, mean_ci
    from bench.cost_model import price_from_total

    llm = _preflight()
    ks = [int(x) for x in KS.split(",") if x.strip()]
    meta = {"experiment": "EX-REGRET — hedge vs AEGIS, regret on compound (§6.9)",
            "model": model, "llm_methods": LLM_METHODS, "det_methods": DET_METHODS,
            "ks": ks, "seeds": SEEDS, "sigma_frac": SIGMA, "family": "compound"}
    cells: dict = {}

    def _write() -> None:
        out_json.write_text(json.dumps({"meta": meta, "cells": cells},
                                       ensure_ascii=False, indent=2), encoding="utf-8")

    for k in ks:
        acc = {m: {"fr": [], "reg": [], "tok": []} for m in LLM_METHODS + DET_METHODS}
        for s in range(SEEDS):
            scn = make_compound_scenario(k, seed=s)
            oracle = solve_oracle(scn, cfg)
            noise = NoiseModel(sigma_frac=SIGMA, seed=1000 + s)
            for m in LLM_METHODS:
                env = build_environment(scn, cfg, noise=noise); llm.reset()
                t0 = time.time()
                try:
                    plan, _ = B.run_method(m, env, llm)
                except Exception as e:                          # noqa: BLE001
                    print(f"  [warn] {m} k={k} s={s}: {type(e).__name__}: {e}", flush=True)
                    plan = Plan.do_nothing(scn)
                f = bool(feasible(scn, plan, cfg))
                acc[m]["fr"].append(f); acc[m]["reg"].append(regret(scn, plan, oracle, cfg))
                acc[m]["tok"].append(float(llm.snapshot()["total"]))
                print(f"  k={k} s={s} {m:26} FR={int(f)} rho={acc[m]['reg'][-1]:.3f} "
                      f"({time.time()-t0:.0f}s)", flush=True)
            for m in DET_METHODS:
                env = build_environment(scn, cfg, noise=noise)
                plan, _ = B.run_method(m, env, None)
                acc[m]["fr"].append(bool(feasible(scn, plan, cfg)))
                acc[m]["reg"].append(regret(scn, plan, oracle, cfg))
                acc[m]["tok"].append(0.0)
        cell = {}
        for m in LLM_METHODS + DET_METHODS:
            ci = fr_cell(acc[m]["fr"])
            rall = mean_ci(acc[m]["reg"])
            feas = [r for f, r in zip(acc[m]["fr"], acc[m]["reg"]) if f]
            rfeas = mean_ci(feas) if feas else None
            cell[m] = {"fr": ci.point, "fr_lo": ci.lo, "fr_hi": ci.hi, "n": ci.n,
                       "regret_all": rall.point, "regret_all_lo": rall.lo, "regret_all_hi": rall.hi,
                       "regret_feasible": (rfeas.point if rfeas else None),
                       "regret_feasible_lo": (rfeas.lo if rfeas else None),
                       "regret_feasible_hi": (rfeas.hi if rfeas else None),
                       "n_feasible": len(feas), "tokens": mean(acc[m]["tok"]),
                       "usd_per_plan": price_from_total(llm.model, mean(acc[m]["tok"]))}
        cells[str(k)] = cell
        for m in LLM_METHODS:                       # catch a silently-broken LLM method
            if cell[m]["n_feasible"] == 0:
                print(f"  [WARN] {m} was NEVER feasible at k={k} (n={cell[m]['n']}). "
                      f"On reasoning-model endpoints this usually means the response was "
                      f"truncated to empty (do-nothing) — raise BASE_MAX_TOKENS and re-run; "
                      f"do NOT trust this row.", flush=True)
        _write()
        print(f"  [checkpoint] k={k} saved", flush=True)
    return cells


def _print_table(cells: dict) -> None:
    print("\n===== REGRET: hedge vs AEGIS (compound, FR + regret rho vs oracle) =====")
    print(f"{'method':27}  k | FR    | regret(feasible) | regret(expected)")
    for k in sorted(cells, key=int):
        for m, c in cells[k].items():
            rf = f"{c['regret_feasible']:.3f}" if c["regret_feasible"] is not None else "  -  "
            print(f"  {m:25} {k} | {c['fr']:.2f}  | {rf:>13}    | {c['regret_all']:.3f}")
        print("  " + "-" * 60)
    print("Reading: a hedging prompt ~matches AEGIS on FR but over-produces (higher")
    print("feasible-only regret), and its residual infeasibility inflates EXPECTED regret;")
    print("AEGIS-Iter/RVoIE are feasible, cheaper, and guaranteed ($0).")


def _figure(cells: dict, model: str, out_png: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:                                       # noqa: BLE001
        print(f"[figure] skipped ({e})"); return
    ks = sorted(cells, key=int)
    methods = LLM_METHODS + DET_METHODS
    colors = {"AEGIS-SinglePassLLM-Hedge": "#ff7f0e", "AEGIS-SinglePassLLM": "#d62728",
              "AEGIS-Iter": "#2ca02c", "AEGIS-RVoIE": "#1f77b4"}
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    nb = len(methods); w = 0.8 / nb
    for i, m in enumerate(methods):
        xs = [j + (i - nb / 2) * w + w / 2 for j in range(len(ks))]
        ys = [cells[k][m]["regret_feasible"] or 0.0 for k in ks]
        ax.bar(xs, ys, width=w, label=m, color=colors.get(m, None), edgecolor="black", linewidth=0.4)
    ax.set_xticks(range(len(ks))); ax.set_xticklabels([f"k={k}" for k in ks])
    ax.set_ylabel("feasible-only regret  ρ  (lower = cheaper)")
    ax.set_title(f"Hedging buys feasibility but over-produces ({model})")
    ax.legend(fontsize=8, loc="upper left"); ax.grid(True, axis="y", alpha=0.3)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    fig.savefig(out_png.with_suffix(".pdf"), bbox_inches="tight")
    print(f"[figure] wrote {out_png}")


def main() -> None:
    _load_env()
    model = (sys.argv[1] if len(sys.argv) > 1 else
             os.environ.get("ONLINE_LLM_MODEL") or MODEL_NAME).strip()
    if not model:
        sys.exit("No model name. Edit MODEL_NAME or pass it: python run_online_regret.py gpt-5.1")
    os.environ["OPENAI_MODEL"] = model
    os.environ["HITACHI_APIM_MODEL"] = model
    os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"] = model
    os.environ["BASE_MAX_TOKENS"] = str(BASE_MAX_TOKENS)

    out_json = ROOT / "results" / f"ex_regret_online_{_slug(model)}.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    n_k = len([x for x in KS.split(",") if x.strip()])
    print("=" * 78)
    print(f"  EX-REGRET — model: {model}")
    print(f"  k = {KS}   seeds = {SEEDS}/cell   sigma = {SIGMA}")
    print(f"  ~{n_k * SEEDS * len(LLM_METHODS)} LLM calls (single-pass) "
          f"+ {n_k * SEEDS * len(DET_METHODS)} deterministic")
    print(f"  output -> {out_json}   (checkpointed after each k)")
    print("=" * 78)

    _install_newer_model_compat()
    t0 = time.time()
    cells = _run(model, out_json)
    print(f"\n[done] regret sweep finished in {(time.time() - t0) / 60:.1f} min")
    _print_table(cells)
    if MAKE_FIGURE:
        _figure(cells, model, ROOT / "results" / "figures" / f"fig_regret_{_slug(model)}.png")
    print("\n" + "=" * 78)
    print(f"  RESULT: {out_json}")
    print(f"  Send me this file — hedge-vs-AEGIS regret for '{model}'.")
    print("=" * 78)


if __name__ == "__main__":
    main()
