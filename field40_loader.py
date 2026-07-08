# bench/field40_loader.py
# -*- coding: utf-8 -*-
"""Load FIELD-40 author-constructed scenarios into bench Scenarios + an OUT-OF-SCHEMA
feasibility scorer that AEGIS's symbol table cannot represent."""
from __future__ import annotations
from typing import Tuple
from bench.scenario import Scenario, Sku, Line, Material, Disruption
from bench.metrics import feasible
from bench.field40_data import FIELD40

_CAT = {"equipment":"EQUIP","labor":"EQUIP","tight-capacity":"EQUIP",
        "supply":"SUPPLY","logistics":"SUPPLY",
        "quality":"DEMAND","compound":"EQUIP",
        "oos-cert":"EQUIP","oos-contam":"EQUIP","oos-compat":"EQUIP","oos-cap":"DEMAND"}

def make_scenario(e: dict) -> Scenario:
    skus=[Sku(sku_id=s[0],demand=float(s[1]),finished_stock=float(s[2]),
              nominal_yield=float(s[3]),true_yield=float(s[4]),
              bom=dict(s[6]),allowed_lines=list(s[5]),unit_cost=float(s[7])) for s in e["skus"]]
    lines=[Line(line_id=l[0],capacity_per_day=float(l[1]),nominal_avail=float(l[2]),
                true_avail=float(l[3]),prod_cost=float(l[4])) for l in e["lines"]]
    mats=[Material(material_id=m[0],onhand=float(m[1]),nominal_lead_time=int(m[2]),
                   true_lead_time=int(m[3])) for m in e["mats"]]
    dis=Disruption(kind=_CAT.get(e["kind"],"EQUIP"),magnitude=0.5,
                   affected=list(e["affected"]),description=e["report"][:140])
    return Scenario(scenario_id=e["id"],category=_CAT.get(e["kind"],"EQUIP"),
                    horizon_days=int(e["horizon"]),skus=skus,lines=lines,materials=mats,disruption=dis)

def oos_ok(plan, e: dict) -> bool:
    """True iff the plan respects the hidden out-of-schema rule (if any)."""
    oos=e.get("oos")
    if not oos: return True
    kind,arg=oos
    if kind=="forbid":
        sku,line=arg
        return float(getattr(plan,"prod_sl",{}).get((sku,line),0.0)) <= 1e-6
    if kind=="sku_cap":
        sku,lim=arg
        return float(getattr(plan,"prod",{}).get(sku,0.0)) <= float(lim)+1e-6
    if kind=="cap_total":
        return float(sum(getattr(plan,"prod",{}).values())) <= float(arg)+1e-6
    return True

def feasible_f40(scn, plan, e: dict, cfg=None) -> bool:
    """In-schema feasibility AND the out-of-schema rule the method never saw."""
    base = feasible(scn, plan) if cfg is None else feasible(scn, plan, cfg)
    return bool(base and oos_ok(plan, e))

def iter_field40():
    for e in FIELD40:
        yield e, make_scenario(e)
