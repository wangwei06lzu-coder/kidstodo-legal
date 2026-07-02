# run_online_nl.py
# -*- coding: utf-8 -*-
"""One-click NL->T2C parse-accuracy check for a HOSTED model (paper §6, EX-NL-LLM).

The flip side of the EX-LLM / agentic results: those show a hosted LLM cannot do the
robust *math*. This shows it CAN do the *parsing* reliably — the LLM-as-front-end half
of AEGIS's "LLM parses natural language -> typed constraints; deterministic math
guarantees robustness" division of labor (the headline NL novelty, Leg C / §4.4).

The chosen model rephrases each specialist gap-answer as free-text prose; the SAME
deterministic T2C compiler AEGIS uses then parses it back. We report parse accuracy
(fraction whose recovered value yields the identical MILP constraint) per model — a
clean certificate that the front-end is sound, with honest reporting of the phrasings
that need the LLM-extraction fallback.

-----------------------------------------------------------------------------
USE (Windows, company key) — identical .env to run_online_llm_all_steps.py:
    python run_online_nl.py gpt-5.1
    python run_online_nl.py claude-opus-4.8
(Or edit MODEL_NAME below and run with no argument.)
Cheap (single-pass, ~60 short calls). Send me results\\ex_nl_online_<model>.json.
-----------------------------------------------------------------------------
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

# ======================= EDIT ME (the one knob you change) ===================
MODEL_NAME = "gpt-4o"          # e.g. "gpt-5.1", "claude-opus-4.8" — or pass as argv[1]
NL_PER_KIND = 20             # paraphrases per gap type (3 types -> 3x this many calls)
BASE_MAX_TOKENS = 2000       # short single-sentence rephrasings; small is fine
# =============================================================================

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


def _preflight() -> None:
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
    res = llm.call_json('You are a specialist. Restate the fact as JSON.',
                        'Fact: line LINE-01 is 80% available. Return {"note":"..."}.')
    dt = time.time() - t0
    if not isinstance(res, dict) or "error" in res:
        sys.exit(f"[preflight] LLM call FAILED ({dt:.1f}s): {res}\n"
                 "  Fix .env / MODEL_NAME and re-run — nothing was spent.")
    print(f"[preflight] OK ({dt:.1f}s)  sample={res}\n", flush=True)


def main() -> None:
    _load_env()
    model = (sys.argv[1] if len(sys.argv) > 1 else
             os.environ.get("ONLINE_LLM_MODEL") or MODEL_NAME).strip()
    if not model:
        sys.exit("No model name. Edit MODEL_NAME or pass it: python run_online_nl.py gpt-5.1")

    os.environ["OPENAI_MODEL"] = model
    os.environ["HITACHI_APIM_MODEL"] = model
    os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"] = model
    os.environ["BASE_MAX_TOKENS"] = str(BASE_MAX_TOKENS)

    out_json = ROOT / "results" / f"ex_nl_online_{_slug(model)}.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    os.environ["NL_LLM"] = "1"
    os.environ["NL_LLM_MODELS"] = model
    os.environ["NL_LLM_N"] = str(NL_PER_KIND)
    os.environ["NL_OUT"] = str(out_json)

    print("=" * 78)
    print(f"  EX-NL-LLM (NL->T2C parse accuracy) — model: {model}")
    print(f"  {NL_PER_KIND} paraphrases/gap-type x 3 types = ~{NL_PER_KIND * 3} short calls")
    print(f"  output -> {out_json}")
    print("=" * 78)

    _install_newer_model_compat()
    _preflight()

    from bench.run_nl_compile import main as run_nl
    t0 = time.time()
    run_nl()                          # regex pass + the model paraphrase pass; writes out_json
    print(f"\n[done] NL parse-accuracy finished in {(time.time() - t0) / 60:.1f} min")

    # Echo the model's headline number.
    try:
        import json
        d = json.loads(out_json.read_text(encoding="utf-8"))
        fam = d.get("summary", {}).get("llm_families", {}).get(model, {})
        if fam:
            print(f"\n  *** {model}: T2C-parse accuracy on model prose = "
                  f"{fam.get('accuracy', 0.0):.2f}  (n={fam.get('n', '?')}) ***")
    except Exception:
        pass

    print("\n" + "=" * 78)
    print(f"  RESULT: {out_json}")
    print(f"  Send me this file — NL->T2C front-end accuracy for '{model}'.")
    print("=" * 78)


if __name__ == "__main__":
    main()
