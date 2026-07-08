# bench/field40_data.py
# -*- coding: utf-8 -*-
"""FIELD-40: 40 author-constructed, incident-report-style compound-emergency scenarios.

PROVENANCE (honest): these are hand-authored by the paper authors to probe OUTSIDE the
COMPOUND-EMERGENCY-1K generator's structure — heterogeneous disruption types, mixed sizes,
free-text-derived phrasing, and (crucially) a subset with genuinely OUT-OF-SCHEMA binding
constraints (certified-operator / contamination / shift-cap rules) that AEGIS's symbol table
cannot represent. They are NOT the 12-practitioner recruited set of Appendix F (that remains
future work); they are an author-constructed external probe. Each entry carries a free-text
report, an adjudicated ground-truth parameterization, and an optional out-of-schema rule.

Each dict:
  id, kind, depth, report            free text + metadata
  horizon                            planning horizon (days)
  skus   [(id, demand, stock, nom_yield, true_yield, [lines], {mat:qty}, cost)]
  lines  [(id, cap, nom_avail, true_avail, prod_cost)]
  mats   [(id, onhand, nom_lead, true_lead)]
  affected [ids that are disrupted / carry a hidden fact]
  oos    None | ("forbid",(sku,line)) | ("sku_cap",(sku,limit)) | ("cap_total",limit)
  note   adjudicator note (why it is feasible / where it bites)
"""

def S(**kw): return kw

FIELD40 = [
# ---- equipment failures (fallback line exists) ---------------------------------
S(id="F01",kind="equipment",depth=1,horizon=5,
  report="Overnight, motor MC-A01 on LINE-A tripped and the maintenance lead estimates the drive is "
         "largely unusable this shift. SKU-100 (demand 120) normally runs on LINE-A; LINE-B is idle.",
  skus=[("SKU-100",120,0,1.0,1.0,["LINE-A","LINE-B"],{"MAT-1":1.0},1.0)],
  lines=[("LINE-A",140,1.0,0.05,0.0),("LINE-B",150,1.0,1.0,0.2)],
  mats=[("MAT-1",800,1,1)],affected=["LINE-A"],oos=None,
  note="LINE-A avail truly 5%; reroute to LINE-B (cap 150>120) is feasible."),
S(id="F02",kind="equipment",depth=0,horizon=5,
  report="A bearing on LINE-C is failing; the line runs but only about 60% of rated output until the "
         "spare arrives. SKU-210 (demand 90) is committed on LINE-C.",
  skus=[("SKU-210",90,0,1.0,1.0,["LINE-C","LINE-D"],{"MAT-2":1.0},1.0)],
  lines=[("LINE-C",130,1.0,0.6,0.0),("LINE-D",120,1.0,1.0,0.3)],
  mats=[("MAT-2",600,1,1)],affected=["LINE-C"],oos=None,
  note="LINE-C at 60% gives 78<90; the gap must be split to LINE-D."),
S(id="F03",kind="equipment",depth=0,horizon=4,
  report="Robotic cell R7 on LINE-E faulted twice today; process engineering thinks it will hold at "
         "roughly a third of throughput. SKU-330 demand is 100.",
  skus=[("SKU-330",100,20,1.0,1.0,["LINE-E","LINE-F"],{"MAT-3":1.0},1.0)],
  lines=[("LINE-E",150,1.0,0.33,0.0),("LINE-F",140,1.0,1.0,0.25)],
  mats=[("MAT-3",700,1,1)],affected=["LINE-E"],oos=None,
  note="20 in stock + LINE-F fallback covers the shortfall from LINE-E's 33%."),
S(id="F04",kind="equipment",depth=1,horizon=5,
  report="LINE-G's conveyor jammed; after reroute of SKU-410 to LINE-H it emerges LINE-H was itself "
         "de-rated last week for a coolant issue nobody logged centrally. Demand 110.",
  skus=[("SKU-410",110,0,1.0,1.0,["LINE-G","LINE-H","LINE-I"],{"MAT-4":1.0},1.0)],
  lines=[("LINE-G",130,1.0,0.0,0.0),("LINE-H",130,1.0,0.5,0.1),("LINE-I",140,1.0,1.0,0.3)],
  mats=[("MAT-4",900,1,1)],affected=["LINE-G","LINE-H"],
  oos=None,note="Depth-1: LINE-G down, reroute exposes LINE-H at 50%; LINE-I saves it."),
S(id="F05",kind="equipment",depth=0,horizon=5,
  report="Preventive-maintenance overran on LINE-J; it will be down most of the window. SKU-520 "
         "(demand 80) has stock 60 and an alternate LINE-K.",
  skus=[("SKU-520",80,60,1.0,1.0,["LINE-J","LINE-K"],{"MAT-5":1.0},1.0)],
  lines=[("LINE-J",100,1.0,0.1,0.0),("LINE-K",90,1.0,1.0,0.4)],
  mats=[("MAT-5",500,1,1)],affected=["LINE-J"],oos=None,
  note="Stock 60 + LINE-K 90 easily covers 80."),
S(id="F06",kind="equipment",depth=2,horizon=6,
  report="LINE-L tripped; reroute to LINE-M, whose true availability turns out reduced; the further "
         "reroute to LINE-N needs material MAT-6 whose replenishment is quietly late. SKU-600 demand 140.",
  skus=[("SKU-600",140,0,1.0,1.0,["LINE-L","LINE-M","LINE-N"],{"MAT-6":1.0},1.0)],
  lines=[("LINE-L",150,1.0,0.0,0.0),("LINE-M",150,1.0,0.4,0.1),("LINE-N",160,1.0,1.0,0.2)],
  mats=[("MAT-6",300,1,1)],affected=["LINE-L","LINE-M","MAT-6"],oos=None,
  note="Depth-2 chain; MAT-6 on-hand 300 still covers the 140 built on LINE-N."),
S(id="F07",kind="equipment",depth=0,horizon=5,
  report="A power dip reset the PLCs on LINE-O; it restarts at partial speed pending re-tuning. "
         "SKU-700 demand 95, alt LINE-P.",
  skus=[("SKU-700",95,0,1.0,1.0,["LINE-O","LINE-P"],{"MAT-7":1.0},1.0)],
  lines=[("LINE-O",120,1.0,0.5,0.0),("LINE-P",110,1.0,1.0,0.3)],
  mats=[("MAT-7",600,1,1)],affected=["LINE-O"],oos=None,note="LINE-O 60<95; split to LINE-P."),
S(id="F08",kind="equipment",depth=1,horizon=5,
  report="Two lines share a compressor that failed: LINE-Q is fully down and LINE-R is throttled. "
         "SKU-800 (demand 130) can also use LINE-S.",
  skus=[("SKU-800",130,0,1.0,1.0,["LINE-Q","LINE-R","LINE-S"],{"MAT-8":1.0},1.0)],
  lines=[("LINE-Q",140,1.0,0.0,0.0),("LINE-R",140,1.0,0.45,0.1),("LINE-S",150,1.0,1.0,0.3)],
  mats=[("MAT-8",900,1,1)],affected=["LINE-Q","LINE-R"],oos=None,
  note="Coupled equipment; LINE-S covers 130."),
# ---- supplier delays (lead > horizon => only on-hand usable) -------------------
S(id="F09",kind="supply",depth=0,horizon=5,
  report="Our MAT-9 supplier emailed that the next truck is stuck at customs; realistic arrival is "
         "well past this build window. SKU-910 (demand 70) consumes 1 MAT-9/unit; on-hand is limited.",
  skus=[("SKU-910",70,0,1.0,1.0,["LINE-T"],{"MAT-9":1.0},1.0)],
  lines=[("LINE-T",120,1.0,1.0,0.0)],mats=[("MAT-9",70,1,9)],affected=["MAT-9"],oos=None,
  note="True lead 9>5 → only 70 on-hand; exactly meets demand, no slack."),
S(id="F10",kind="supply",depth=0,horizon=5,
  report="Second-source component MAT-10 slipped; procurement believes on-hand covers most but not all "
         "of SKU-1010's demand 100 (BOM 1.0). A substitute is not qualified.",
  skus=[("SKU-1010",100,15,1.0,1.0,["LINE-U"],{"MAT-10":1.0},1.0)],
  lines=[("LINE-U",130,1.0,1.0,0.0)],mats=[("MAT-10",90,2,8)],affected=["MAT-10"],oos=None,
  note="On-hand 90 + stock 15 covers 100."),
S(id="F11",kind="supply",depth=0,horizon=6,
  report="MAT-11's inbound shipment is delayed well past the build window; the plant must carry "
         "SKU-1110 (demand 85) on the on-hand lot alone, with a thin buffer.",
  skus=[("SKU-1110",85,0,1.0,1.0,["LINE-V"],{"MAT-11":1.0},1.0)],
  lines=[("LINE-V",120,1.0,1.0,0.0)],mats=[("MAT-11",90,1,8)],
  affected=["MAT-11"],oos=None,
  note="True lead 8>6 → only 90 on-hand usable; 90 ≥ 85 demand, feasible."),
S(id="F12",kind="supply",depth=0,horizon=4,
  report="A resin lot (MAT-13) was quarantined by incoming QC; usable on-hand is only part of what "
         "SKU-1210 (demand 60) needs. Stock buffers some finished units.",
  skus=[("SKU-1210",60,25,1.0,1.0,["LINE-W"],{"MAT-13":1.0},1.0)],
  lines=[("LINE-W",100,1.0,1.0,0.0)],mats=[("MAT-13",40,1,7)],affected=["MAT-13"],oos=None,
  note="Stock 25 + usable 40 = 65 ≥ 60."),
S(id="F13",kind="supply",depth=0,horizon=5,
  report="Ocean freight delay on MAT-14; the true ETA is beyond the horizon. SKU-1310 demand 50, "
         "on-hand tight, no finished stock.",
  skus=[("SKU-1310",50,0,1.0,1.0,["LINE-X"],{"MAT-14":1.0},1.0)],
  lines=[("LINE-X",90,1.0,1.0,0.0)],mats=[("MAT-14",55,2,10)],affected=["MAT-14"],oos=None,
  note="On-hand 55 ≥ 50; feasible only because buffer existed."),
S(id="F14",kind="supply",depth=1,horizon=6,
  report="MAT-15 shortage forces SKU-1410 onto an alternate line LINE-Z; that line then reveals a "
         "secondary material MAT-16 that is itself back-ordered. Demand 90.",
  skus=[("SKU-1410",90,0,1.0,1.0,["LINE-Y","LINE-Z"],{"MAT-15":1.0,"MAT-16":0.5},1.0)],
  lines=[("LINE-Y",100,1.0,0.2,0.0),("LINE-Z",120,1.0,1.0,0.2)],
  mats=[("MAT-15",95,1,1),("MAT-16",50,1,7)],affected=["LINE-Y","MAT-16"],oos=None,
  note="LINE-Y down→LINE-Z; MAT-16 need 45≤50 on-hand."),
S(id="F15",kind="supply",depth=0,horizon=5,
  report="A key adhesive (MAT-17) supplier declared force majeure. On-hand plus finished stock must "
         "carry SKU-1510 (demand 110).",
  skus=[("SKU-1510",110,40,1.0,1.0,["LINE-AA"],{"MAT-17":1.0},1.0)],
  lines=[("LINE-AA",140,1.0,1.0,0.0)],mats=[("MAT-17",80,1,12)],affected=["MAT-17"],oos=None,
  note="Stock 40 + on-hand 80 = 120 ≥ 110."),
S(id="F16",kind="supply",depth=0,horizon=5,
  report="Rail strike delays MAT-18. SKU-1610 (demand 65) has enough on-hand only if scrap from a "
         "known yield issue is accounted for.",
  skus=[("SKU-1610",65,0,1.0,0.85,["LINE-AB"],{"MAT-18":1.0},1.0)],
  lines=[("LINE-AB",120,1.0,1.0,0.0)],mats=[("MAT-18",90,1,9)],affected=["MAT-18","SKU-1610"],oos=None,
  note="Lead + a 0.85 yield: need 65/0.85≈76 material ≤90 on-hand."),
# ---- quality / yield defects ---------------------------------------------------
S(id="F17",kind="quality",depth=0,horizon=5,
  report="First-piece inspection on SKU-1710 shows a plating defect; QC estimates true yield near "
         "70%. Demand 100, ample line and material.",
  skus=[("SKU-1710",100,0,1.0,0.70,["LINE-AC"],{"MAT-19":1.0},1.0)],
  lines=[("LINE-AC",200,1.0,1.0,0.0)],mats=[("MAT-19",900,1,1)],affected=["SKU-1710"],oos=None,
  note="Over-build to 100/0.70≈143 within cap 200."),
S(id="F18",kind="quality",depth=0,horizon=5,
  report="A raw-material variation is causing SKU-1810 to fail final test at elevated rates; effective "
         "yield ≈ 60%. Demand 90.",
  skus=[("SKU-1810",90,0,1.0,0.60,["LINE-AD"],{"MAT-20":1.0},1.0)],
  lines=[("LINE-AD",180,1.0,1.0,0.0)],mats=[("MAT-20",900,1,1)],affected=["SKU-1810"],oos=None,
  note="Need 150 builds ≤ cap 180."),
S(id="F19",kind="quality",depth=0,horizon=4,
  report="Operator flagged cosmetic rejects climbing on SKU-1910; true yield perhaps 80%. Demand 120, "
         "some finished stock.",
  skus=[("SKU-1910",120,30,1.0,0.80,["LINE-AE"],{"MAT-21":1.0},1.0)],
  lines=[("LINE-AE",160,1.0,1.0,0.0)],mats=[("MAT-21",900,1,1)],affected=["SKU-1910"],oos=None,
  note="(120-30)/0.80≈113 ≤ 160."),
S(id="F20",kind="quality",depth=1,horizon=5,
  report="SKU-2010 yield dropped; shifting volume to LINE-AG (allowed) uncovers that LINE-AG runs the "
         "part at a still-lower yield due to a fixturing difference. Demand 100.",
  skus=[("SKU-2010",100,0,1.0,0.75,["LINE-AF","LINE-AG"],{"MAT-22":1.0},1.0)],
  lines=[("LINE-AF",120,1.0,0.0,0.0),("LINE-AG",220,1.0,1.0,0.2)],
  mats=[("MAT-22",900,1,1)],affected=["LINE-AF","SKU-2010"],oos=None,
  note="LINE-AF down; LINE-AG at yield 0.75 needs ~134 ≤ 220."),
S(id="F21",kind="quality",depth=0,horizon=5,
  report="Calibration drift on a test rig means SKU-2110 true yield ~65%; demand 80.",
  skus=[("SKU-2110",80,0,1.0,0.65,["LINE-AH"],{"MAT-23":1.0},1.0)],
  lines=[("LINE-AH",160,1.0,1.0,0.0)],mats=[("MAT-23",900,1,1)],affected=["SKU-2110"],oos=None,
  note="123 builds ≤ 160."),
S(id="F22",kind="quality",depth=0,horizon=5,
  report="A supplier changed a coating without notice; SKU-2210 now yields ~72%. Demand 110 with stock 20.",
  skus=[("SKU-2210",110,20,1.0,0.72,["LINE-AI"],{"MAT-24":1.0},1.0)],
  lines=[("LINE-AI",180,1.0,1.0,0.0)],mats=[("MAT-24",900,1,1)],affected=["SKU-2210"],oos=None,
  note="(110-20)/0.72≈125 ≤ 180."),
S(id="F23",kind="quality",depth=0,horizon=4,
  report="Humidity excursion overnight raised scrap on SKU-2310; true yield ≈ 78%. Demand 100.",
  skus=[("SKU-2310",100,0,1.0,0.78,["LINE-AJ"],{"MAT-25":1.0},1.0)],
  lines=[("LINE-AJ",150,1.0,1.0,0.0)],mats=[("MAT-25",900,1,1)],affected=["SKU-2310"],oos=None,
  note="128 ≤ 150."),
S(id="F24",kind="quality",depth=1,horizon=6,
  report="SKU-2410 fails at 30% on its primary line; rerouting to LINE-AL is fine BUT that line's "
         "feeder material MAT-26 is short. Demand 90.",
  skus=[("SKU-2410",90,0,1.0,0.70,["LINE-AK","LINE-AL"],{"MAT-26":1.0},1.0)],
  lines=[("LINE-AK",100,1.0,0.0,0.0),("LINE-AL",200,1.0,1.0,0.2)],
  mats=[("MAT-26",150,1,1)],affected=["LINE-AK","SKU-2410"],oos=None,
  note="Need 90/0.70≈129 material ≤150; feasible."),
# ---- compound (2-3 co-occurring; breadth+depth) --------------------------------
S(id="F25",kind="compound",depth=1,horizon=6,
  report="Simultaneously: LINE-AM down (equipment), SKU-2510 yield off (quality), and a shared "
         "material MAT-27 slightly late. Demand 120; fallback LINE-AN.",
  skus=[("SKU-2510",120,0,1.0,0.80,["LINE-AM","LINE-AN"],{"MAT-27":1.0},1.0)],
  lines=[("LINE-AM",130,1.0,0.0,0.0),("LINE-AN",220,1.0,1.0,0.2)],
  mats=[("MAT-27",300,2,3)],affected=["LINE-AM","SKU-2510","MAT-27"],oos=None,
  note="Three coupled facts; LINE-AN at 0.80 yield needs 150 ≤220, MAT-27 lead 3≤6."),
S(id="F26",kind="compound",depth=0,horizon=5,
  report="Two independent SKUs are hit at once: SKU-2610 by a yield drop and SKU-2620 by a line "
         "de-rate — a breadth-2 event on separate resources.",
  skus=[("SKU-2610",100,0,1.0,0.75,["LINE-AO"],{"MAT-28":1.0},1.0),
        ("SKU-2620",90,0,1.0,1.0,["LINE-AP"],{"MAT-29":1.0},1.0)],
  lines=[("LINE-AO",180,1.0,1.0,0.0),("LINE-AP",130,1.0,0.6,0.0)],
  mats=[("MAT-28",900,1,1),("MAT-29",900,1,1)],affected=["SKU-2610","LINE-AP"],oos=None,
  note="Two independent gaps; each has headroom."),
S(id="F27",kind="compound",depth=2,horizon=7,
  report="A cascade: LINE-AQ fails → reroute SKU-2710 to LINE-AR (also degraded) → LINE-AS needs "
         "MAT-30 whose lead is long. Demand 130.",
  skus=[("SKU-2710",130,0,1.0,1.0,["LINE-AQ","LINE-AR","LINE-AS"],{"MAT-30":1.0},1.0)],
  lines=[("LINE-AQ",140,1.0,0.0,0.0),("LINE-AR",140,1.0,0.35,0.1),("LINE-AS",160,1.0,1.0,0.2)],
  mats=[("MAT-30",200,1,1)],affected=["LINE-AQ","LINE-AR"],oos=None,
  note="Depth-2 equipment cascade; MAT-30 on-hand 200≥130."),
S(id="F28",kind="compound",depth=1,horizon=6,
  report="A quality hold on SKU-2810 plus a late material MAT-31 for the SAME SKU; the plan must "
         "elicit two specialist facts. Demand 100, stock 10.",
  skus=[("SKU-2810",100,10,1.0,0.70,["LINE-AT"],{"MAT-31":1.0},1.0)],
  lines=[("LINE-AT",220,1.0,1.0,0.0)],mats=[("MAT-31",160,1,6)],affected=["SKU-2810","MAT-31"],oos=None,
  note="Need (100-10)/0.70≈129 material ≤160; yield ok on cap 220."),
S(id="F29",kind="compound",depth=0,horizon=5,
  report="Breadth-3: three products each lightly disrupted (one yield, one line, one material). "
         "Coordinator must resolve all before committing.",
  skus=[("SKU-2910",80,0,1.0,0.8,["LINE-AU"],{"MAT-32":1.0},1.0),
        ("SKU-2920",70,0,1.0,1.0,["LINE-AV"],{"MAT-33":1.0},1.0),
        ("SKU-2930",60,0,1.0,1.0,["LINE-AW"],{"MAT-34":1.0},1.0)],
  lines=[("LINE-AU",160,1.0,1.0,0.0),("LINE-AV",120,1.0,0.7,0.0),("LINE-AW",100,1.0,1.0,0.0)],
  mats=[("MAT-32",900,1,1),("MAT-33",900,1,1),("MAT-34",70,1,6)],
  affected=["SKU-2910","LINE-AV","MAT-34"],oos=None,note="Breadth-3, all in-schema, all feasible."),
S(id="F30",kind="compound",depth=1,horizon=6,
  report="A logistics delay strands finished-goods truck AND a line trips; SKU-3010 must be rebuilt "
         "on a fallback while a shared material is slightly late. Demand 115.",
  skus=[("SKU-3010",115,0,1.0,1.0,["LINE-AX","LINE-AY"],{"MAT-35":1.0},1.0)],
  lines=[("LINE-AX",130,1.0,0.0,0.0),("LINE-AY",200,1.0,1.0,0.2)],
  mats=[("MAT-35",400,2,4)],affected=["LINE-AX","MAT-35"],oos=None,
  note="LINE-AX down→LINE-AY; MAT-35 lead 4≤6."),
# ---- labor / logistics (modeled as availability / lead reductions) -------------
S(id="F31",kind="labor",depth=0,horizon=5,
  report="A flu outbreak cut the LINE-AZ crew; effective availability ~50%. SKU-3110 (demand 85) has "
         "an alternate crewed line LINE-BA.",
  skus=[("SKU-3110",85,0,1.0,1.0,["LINE-AZ","LINE-BA"],{"MAT-36":1.0},1.0)],
  lines=[("LINE-AZ",120,1.0,0.5,0.0),("LINE-BA",110,1.0,1.0,0.3)],
  mats=[("MAT-36",900,1,1)],affected=["LINE-AZ"],oos=None,note="Crew-limited avail; LINE-BA covers."),
S(id="F32",kind="labor",depth=0,horizon=5,
  report="Only one certified technician for LINE-BB today; it runs a single shift (~40% window). "
         "SKU-3210 demand 70, fallback LINE-BC.",
  skus=[("SKU-3210",70,0,1.0,1.0,["LINE-BB","LINE-BC"],{"MAT-37":1.0},1.0)],
  lines=[("LINE-BB",130,1.0,0.4,0.0),("LINE-BC",100,1.0,1.0,0.3)],
  mats=[("MAT-37",900,1,1)],affected=["LINE-BB"],oos=None,note="Single-shift avail; LINE-BC 100≥70."),
S(id="F33",kind="logistics",depth=0,horizon=5,
  report="Inbound dock congestion delays MAT-38 unloading; effective lead exceeds the window. "
         "SKU-3310 (demand 60) leans on on-hand.",
  skus=[("SKU-3310",60,0,1.0,1.0,["LINE-BD"],{"MAT-38":1.0},1.0)],
  lines=[("LINE-BD",100,1.0,1.0,0.0)],mats=[("MAT-38",65,1,7)],affected=["MAT-38"],oos=None,
  note="On-hand 65≥60."),
S(id="F34",kind="labor",depth=1,horizon=6,
  report="A walkout idles LINE-BE; the fallback LINE-BF is staffed but on reduced hours, and a shared "
         "material is late. SKU-3410 demand 100.",
  skus=[("SKU-3410",100,0,1.0,1.0,["LINE-BE","LINE-BF"],{"MAT-39":1.0},1.0)],
  lines=[("LINE-BE",120,1.0,0.0,0.0),("LINE-BF",160,1.0,0.7,0.1)],
  mats=[("MAT-39",130,2,4)],affected=["LINE-BE","LINE-BF"],oos=None,
  note="LINE-BF at 70% gives 112≥100; material 4≤6."),
S(id="F35",kind="logistics",depth=0,horizon=5,
  report="A snowstorm delays outbound but also the inbound of MAT-40; some finished stock buffers "
         "SKU-3510 (demand 95).",
  skus=[("SKU-3510",95,35,1.0,1.0,["LINE-BG"],{"MAT-40":1.0},1.0)],
  lines=[("LINE-BG",140,1.0,1.0,0.0)],mats=[("MAT-40",70,1,8)],affected=["MAT-40"],oos=None,
  note="Stock 35 + on-hand 70 = 105≥95."),
# ---- OUT-OF-SCHEMA binding constraints (AEGIS cannot represent these) -----------
S(id="F36",kind="oos-cert",depth=1,horizon=5,
  report="LINE-BH is down so SKU-3610 must move to LINE-BI. LINE-BI is nominally capable, but a "
         "regulator requires a CERTIFIED operator for this part and none is on shift — an approval "
         "constraint absent from the planning model. Demand 100.",
  skus=[("SKU-3610",100,0,1.0,1.0,["LINE-BH","LINE-BI","LINE-BJ"],{"MAT-41":1.0},1.0)],
  lines=[("LINE-BH",120,1.0,0.0,0.0),("LINE-BI",160,1.0,1.0,0.0),("LINE-BJ",130,1.0,1.0,0.4)],
  mats=[("MAT-41",900,1,1)],affected=["LINE-BH"],oos=("forbid",("SKU-3610","LINE-BI")),
  note="AEGIS routes to cheaper LINE-BI (allowed publicly) → violates the hidden cert rule; "
       "the compliant plan uses costlier LINE-BJ. Fallback flags residual risk, cannot solve it."),
S(id="F37",kind="oos-contam",depth=0,horizon=5,
  report="SKU-3710 shares LINE-BK with an allergen product; a food-safety rule forbids running it "
         "there without a full changeover not scheduled today — a rule not in the optimizer. LINE-BL "
         "is compliant but pricier. Demand 90.",
  skus=[("SKU-3710",90,0,1.0,1.0,["LINE-BK","LINE-BL"],{"MAT-42":1.0},1.0)],
  lines=[("LINE-BK",150,1.0,1.0,0.0),("LINE-BL",130,1.0,1.0,0.5)],
  mats=[("MAT-42",900,1,1)],affected=["LINE-BK"],oos=("forbid",("SKU-3710","LINE-BK")),
  note="Contamination rule forbids LINE-BK; AEGIS picks it (cheapest) → infeasible under the rule."),
S(id="F38",kind="oos-compat",depth=1,horizon=5,
  report="After LINE-BM fails, SKU-3810 could run on LINE-BN, but a documented tooling incompatibility "
         "(known only to the cell lead) makes that combination invalid — not encoded anywhere. LINE-BO "
         "works. Demand 110.",
  skus=[("SKU-3810",110,0,1.0,1.0,["LINE-BM","LINE-BN","LINE-BO"],{"MAT-43":1.0},1.0)],
  lines=[("LINE-BM",130,1.0,0.0,0.0),("LINE-BN",170,1.0,1.0,0.0),("LINE-BO",140,1.0,1.0,0.4)],
  mats=[("MAT-43",900,1,1)],affected=["LINE-BM"],oos=("forbid",("SKU-3810","LINE-BN")),
  note="Machine-incompat forbids LINE-BN; AEGIS's cheapest reroute lands there → fails."),
S(id="F39",kind="oos-cap",depth=0,horizon=5,
  report="An emergency overtime cap (union agreement) limits TOTAL plant starts this window to 210 "
         "units across all products — a global rule outside the per-line model. SKU-3910 (demand 100) "
         "and SKU-3920 (demand 90) each carry a mild yield hold, so a SAFETY-margin plan over-starts.",
  skus=[("SKU-3910",100,0,1.0,0.95,["LINE-BP"],{"MAT-44":1.0},1.0),
        ("SKU-3920",90,0,1.0,0.95,["LINE-BQ"],{"MAT-45":1.0},1.0)],
  lines=[("LINE-BP",220,1.0,1.0,0.0),("LINE-BQ",200,1.0,1.0,0.0)],
  mats=[("MAT-44",900,1,1),("MAT-45",900,1,1)],affected=["SKU-3910","SKU-3920"],
  oos=("cap_total",210),
  note="Exact-demand plan starts 106+95=201 ≤ 210 (compliant, feasible). AEGIS's robust safety "
       "margin over-starts to ~245 > 210 → busts the hidden global cap it cannot see."),
# ---- tight-capacity: robust margin over-produces into capacity (§6.8 mode) ------
S(id="F40",kind="tight-capacity",depth=1,horizon=5,
  report="LINE-BR fails; the only fallback LINE-BS is barely adequate (headroom ~1.05). With a yield "
         "hold on SKU-4010 the robust safety margin over-builds and exceeds LINE-BS's true capacity. "
         "Demand 100.",
  skus=[("SKU-4010",100,0,1.0,0.85,["LINE-BR","LINE-BS"],{"MAT-46":1.0},1.0)],
  lines=[("LINE-BR",120,1.0,0.0,0.0),("LINE-BS",124,1.0,1.0,0.2)],
  mats=[("MAT-46",900,1,1)],affected=["LINE-BR","SKU-4010"],oos=None,
  note="Robust over-build to ~100/(0.85-margin) exceeds cap 124 → capacity failure (§6.8); "
       "full-information oracle at true yield 0.85 needs 118≤124 and is feasible."),
]

assert len(FIELD40) == 40, len(FIELD40)
