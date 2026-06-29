# run_online_toolagent.py
# -*- coding: utf-8 -*-
"""One-click AGENTIC tool-use baseline for a HOSTED model (paper §6.9, the "fair
control" the review asks for).

The single-pass EX-LLM sweep (run_online_llm_all_steps.py) shows a frontier model
that AUTHORS a plan from given findings collapses with coupling. The obvious
rebuttal is "a real agent would call the tools itself and iterate." This script
answers that: it runs the **multi-round ReAct tool-using agent** (`LLM-ToolAgent` /
`LLM-ToolAgent-Iter`) — the LLM sees the SAME metered `env.query(agent,key)` door
AEGIS uses, decides which specialists to consult, reads the answers, and either
consults more or commits — against the deterministic `AEGIS-Iter` reference.

Primary grid is the DEPTH cascade: the agent consults the publicly *named* gap but
does not walk a depth>0 cascade it was never told exists, so it plateaus while
AEGIS-Iter (schema-grounded re-detection) holds FR=1.00. That separates the result
from capability, from query-access, AND from iteration. Optionally also runs the
BREADTH grid (k), directly comparable to Table 6.

  Output: results/ex_toolagent_online_<model>.json   (checkpointed after each level)
          results/figures/fig_toolagent_<model>.png

-----------------------------------------------------------------------------
USE (Windows, company key) — identical .env to run_online_llm_all_steps.py:
    python run_online_toolagent.py gpt-5.1
    python run_online_toolagent.py claude-opus-4.8
(Or edit MODEL_NAME below and run with no argument.)
Send me results\\ex_toolagent_online_<model>.json when done.
-----------------------------------------------------------------------------
NOTE ON COST: the agent is MULTI-TURN (a few LLM calls per scenario), so it costs
several times a single-pass call. Defaults are deliberately small (1 method, depth
grid, 30 seeds). Run it on just 1–2 frontier models — the point is "even the
strongest agent plateaus," which does not need all six or n=50.
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
# ----------------------- experiment conditions -------------------------------
GRID = "depth"               # "depth" (recommended) | "breadth" | "both"
DEPTHS = "1,2,3,4"           # cascade depths to sweep
BREADTHS = "1,2,3,4,6,8"     # coupling levels (only used for breadth/both)
METHODS = ["LLM-ToolAgent-Iter"]   # add "LLM-ToolAgent" to also run the no-nudge variant
SEEDS = 30                   # distinct scenarios per cell (multi-turn -> keep modest)
SIGMA = 0.15                 # answer-noise std (fraction) — same as the other families
BASE_MAX_TOKENS = 8000       # budget per call (reasoning models need room)
MAKE_FIGURE = True
# =============================================================================

ROOT = Path(__file__).resolve().parent          # .../coordinator_scenario
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ----------------------------------------------------------------------------- env / compat / preflight
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
    max_completion_tokens only on a param error. gpt-4o path unchanged; metering
    preserved (same metered client.chat.completions.create is called)."""
    import agents_llm.llm_agent_base as lab
    Base = lab.LLMAgentBase
    if getattr(Base, "_online_compat_installed", False):
        return
    _PARAM_ERR = ("max_tokens", "max_completion_tokens", "temperature",
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
                    if not any(t in str(e).lower() for t in _PARAM_ERR):
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
    api_type = os.environ.get("OPENAI_API_TYPE", "openai").lower()
    base = os.environ.get("OPENAI_BASE_URL", "<default: Hitachi v1 endpoint>")
    print(f"[preflight] backend={api_type}  model={llm.model}  endpoint={base}")
    t0 = time.time()
    res = llm.call_json("Reply with strict JSON only.",
                        'Return exactly {"ok": true} and nothing else.')
    dt = time.time() - t0
    if not isinstance(res, dict) or "error" in res:
        sys.exit(f"[preflight] LLM call FAILED ({dt:.1f}s): {res}\n"
                 "  Fix .env / MODEL_NAME and re-run — no scenarios were spent.")
    print(f"[preflight] OK ({dt:.1f}s)  sample={res}\n", flush=True)
    return llm


# ----------------------------------------------------------------------------- the sweep
def _run(model: str, out_json: Path) -> dict:
    from bench import baselines as B
    from bench.config import BENCH as cfg
    from bench.environment import NoiseModel, build_environment
    from bench.metrics import feasible
    from bench.milp_model import Plan
    from bench.scenario_gen import make_compound_scenario, make_depth_scenario
    from bench.stats import fr_cell
    from bench.cost_model import price_from_total

    llm = _preflight()
    grids = []
    if GRID in ("depth", "both"):
        grids.append(("depth", make_depth_scenario,
                      [int(x) for x in DEPTHS.split(",") if x.strip()]))
    if GRID in ("breadth", "both"):
        grids.append(("breadth", make_compound_scenario,
                      [int(x) for x in BREADTHS.split(",") if x.strip()]))

    meta = {"experiment": "EX-TOOLAGENT — agentic ReAct tool-use vs AEGIS-Iter (§6.9)",
            "model": model, "methods": METHODS, "grid": GRID, "depths": DEPTHS,
            "breadths": BREADTHS, "seeds": SEEDS, "sigma_frac": SIGMA}
    results: dict = {}

    def _write() -> None:
        out_json.write_text(json.dumps({"meta": meta, "results": results},
                                       ensure_ascii=False, indent=2), encoding="utf-8")

    for gname, gen, levels in grids:
        res = results.setdefault(gname, {})
        for lv in levels:
            acc = {m: {"fr": [], "tok": [], "q": [], "rounds": [], "sec": []} for m in METHODS}
            iter_fr = []
            for s in range(SEEDS):
                scn = gen(lv, seed=s)
                noise = NoiseModel(sigma_frac=SIGMA, seed=1000 + s)
                for m in METHODS:
                    env = build_environment(scn, cfg, noise=noise); llm.reset()
                    t0 = time.time()
                    try:
                        plan, rounds = B.run_method(m, env, llm)
                    except Exception as e:                      # noqa: BLE001
                        print(f"  [warn] {m} {gname}={lv} s={s}: {type(e).__name__}: {e}", flush=True)
                        plan, rounds = Plan.do_nothing(scn), 0
                    snap = llm.snapshot()
                    acc[m]["fr"].append(1.0 if feasible(scn, plan, cfg) else 0.0)
                    acc[m]["tok"].append(float(snap["total"]))
                    acc[m]["q"].append(float(env.meter.n_queries))
                    acc[m]["rounds"].append(float(rounds))
                    acc[m]["sec"].append(float(snap.get("llm_sec", 0.0)))
                    print(f"  {gname}={lv} s={s} {m}: FR={acc[m]['fr'][-1]:.0f} "
                          f"q={env.meter.n_queries} rounds={rounds} "
                          f"tok={snap['total']:.0f} ({time.time()-t0:.0f}s)", flush=True)
                env2 = build_environment(scn, cfg, noise=noise)
                plan_i, _ = B.run_method("AEGIS-Iter", env2, None)
                iter_fr.append(1.0 if feasible(scn, plan_i, cfg) else 0.0)

            for m in METHODS:
                ci = fr_cell([bool(x) for x in acc[m]["fr"]])
                res.setdefault(m, {"cells": {}})["cells"][str(lv)] = {
                    "fr": ci.point, "fr_lo": ci.lo, "fr_hi": ci.hi, "n": ci.n,
                    "tokens": mean(acc[m]["tok"]), "n_queries": mean(acc[m]["q"]),
                    "rounds": mean(acc[m]["rounds"]), "llm_sec": mean(acc[m]["sec"]),
                    "usd_per_plan": price_from_total(llm.model, mean(acc[m]["tok"]))}
            ici = fr_cell([bool(x) for x in iter_fr])
            res.setdefault("AEGIS-Iter", {"cells": {}})["cells"][str(lv)] = {
                "fr": ici.point, "fr_lo": ici.lo, "fr_hi": ici.hi, "n": ici.n}
            _write()
            print(f"  [checkpoint] {gname}={lv} saved", flush=True)

    return results


def _print_tables(results: dict) -> None:
    for gname, res in results.items():
        axis = "depth d" if gname == "depth" else "coupling k"
        levels = sorted({int(x) for m in res for x in res[m]["cells"]})
        print(f"\n===== {gname.upper()}: FR vs {axis} "
              f"(n={SEEDS}/cell, 95% Wilson CI) =====")
        for m in [k for k in res if k != "AEGIS-Iter"] + ["AEGIS-Iter"]:
            cells = res[m]["cells"]
            row = "  ".join(
                f"{axis.split()[0]}={lv}:{cells[str(lv)]['fr']:.2f}"
                f"[{cells[str(lv)]['fr_lo']:.2f},{cells[str(lv)]['fr_hi']:.2f}]"
                for lv in levels if str(lv) in cells)
            print(f"  {m:22} {row}")
        for m in [k for k in res if k != "AEGIS-Iter"]:
            c = res[m]["cells"]
            tok = mean(c[str(lv)]["tokens"] for lv in levels if str(lv) in c)
            q = mean(c[str(lv)]["n_queries"] for lv in levels if str(lv) in c)
            rd = mean(c[str(lv)]["rounds"] for lv in levels if str(lv) in c)
            usd = mean(c[str(lv)]["usd_per_plan"] for lv in levels if str(lv) in c)
            print(f"  {m:22} avg tokens/plan={tok:.0f}  queries={q:.1f}  "
                  f"rounds={rd:.1f}  $/plan={usd:.4f}  (AEGIS-Iter = $0, deterministic)")


def _figure(results: dict, model: str, out_png: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:                                       # noqa: BLE001
        print(f"[figure] skipped ({e}); the JSON is what matters."); return
    grids = list(results)
    fig, axes = plt.subplots(1, len(grids), figsize=(6.4 * len(grids), 4.2), squeeze=False)
    palette = ["#ff7f0e", "#8c564b", "#9467bd"]
    for ax, g in zip(axes[0], grids):
        res = results[g]
        for i, m in enumerate([k for k in res if k != "AEGIS-Iter"]):
            cm = res[m]["cells"]; xs = sorted(int(x) for x in cm)
            ys = [cm[str(x)]["fr"] for x in xs]
            lo = [max(0.0, cm[str(x)]["fr"] - cm[str(x)]["fr_lo"]) for x in xs]
            hi = [max(0.0, cm[str(x)]["fr_hi"] - cm[str(x)]["fr"]) for x in xs]
            ax.errorbar(xs, ys, yerr=[lo, hi], marker="s", linestyle="--", capsize=3,
                        linewidth=2, color=palette[i % len(palette)], label=m)
        a = res["AEGIS-Iter"]["cells"]; xs = sorted(int(x) for x in a)
        ays = [a[str(x)]["fr"] for x in xs]
        alo = [max(0.0, a[str(x)]["fr"] - a[str(x)]["fr_lo"]) for x in xs]
        ahi = [max(0.0, a[str(x)]["fr_hi"] - a[str(x)]["fr"]) for x in xs]
        ax.errorbar(xs, ays, yerr=[alo, ahi], marker="o", linestyle="-", capsize=3,
                    linewidth=2.5, color="#2ca02c", label="AEGIS-Iter (deterministic)")
        ax.set_xticks(xs); ax.set_xticklabels(xs)
        if g == "breadth":
            ax.set_xscale("log", base=2); ax.set_xticklabels(xs)
        ax.set_xlabel("depth d (cascade)" if g == "depth" else "coupling k (breadth)")
        ax.set_ylabel("Feasibility rate (FR)"); ax.set_ylim(-0.04, 1.06)
        ax.grid(True, alpha=0.3); ax.legend(fontsize=8)
        ax.set_title(f"{g}")
    fig.suptitle(f"Agentic ReAct tool-use ({model}) plateaus; AEGIS-Iter holds "
                 f"(n={SEEDS}/cell)")
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    fig.savefig(out_png.with_suffix(".pdf"), bbox_inches="tight")
    print(f"[figure] wrote {out_png}")


def main() -> None:
    _load_env()
    model = (sys.argv[1] if len(sys.argv) > 1 else
             os.environ.get("ONLINE_LLM_MODEL") or MODEL_NAME).strip()
    if not model:
        sys.exit("No model name. Edit MODEL_NAME or pass it: "
                 "python run_online_toolagent.py gpt-5.1")
    os.environ["OPENAI_MODEL"] = model
    os.environ["HITACHI_APIM_MODEL"] = model
    os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"] = model
    os.environ["BASE_MAX_TOKENS"] = str(BASE_MAX_TOKENS)

    out_json = ROOT / "results" / f"ex_toolagent_online_{_slug(model)}.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)

    levels = (len(DEPTHS.split(",")) if GRID in ("depth", "both") else 0) + \
             (len(BREADTHS.split(",")) if GRID in ("breadth", "both") else 0)
    print("=" * 78)
    print(f"  EX-TOOLAGENT (agentic) — model: {model}")
    print(f"  methods = {METHODS}   grid = {GRID}   seeds = {SEEDS}/cell   sigma = {SIGMA}")
    print(f"  ~{levels * SEEDS * len(METHODS)} agentic scenarios (multi-turn) "
          f"+ {levels * SEEDS} deterministic AEGIS-Iter")
    print(f"  output -> {out_json}   (checkpointed after each level)")
    print("=" * 78)

    _install_newer_model_compat()
    t0 = time.time()
    results = _run(model, out_json)
    print(f"\n[done] agentic sweep finished in {(time.time() - t0) / 60:.1f} min")
    _print_tables(results)
    if MAKE_FIGURE:
        _figure(results, model, ROOT / "results" / "figures" / f"fig_toolagent_{_slug(model)}.png")

    print("\n" + "=" * 78)
    print(f"  RESULT: {out_json}")
    print(f"  Send me this file — agentic tool-use vs AEGIS-Iter for '{model}'.")
    print("=" * 78)


if __name__ == "__main__":
    main()
