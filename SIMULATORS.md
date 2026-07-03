# Simulator Replacement Status

Simulators displaced or targeted for displacement by the Cameron EDA / ltz / cameron-grid federated stack. Effort estimates are for production-quality displacement including validation; proof-of-concept effort is typically 30–50% of the figures shown.

License cost estimates are per-seat annual figures sourced from published pricing, industry surveys, and vendor quotes where available. Actual costs vary significantly by tier, bundle, and negotiation.

---

## SPICE / Analog Circuit

| Simulator | Vendor | Replacement | Status | Effort | Est. License Cost/Seat/Yr | Customer Savings Note |
|---|---|---|---|---|---|---|
| **LTspice** | Analog Devices | ltz (Xyce + libxycespice.so) | ✅ Done | — | Free | Quality / performance uplift; Xyce converges on stiff circuits LTspice fails |
| **QSpice** | Qorvo | ltz (Xyce + libxycespice.so) | ✅ Done | — | Free | Quality / performance uplift; open vs. closed ecosystem |
| **SIMetrix / SIMPLIS** | Mentor/SIMETRIX | ltz (Xyce + NVC behavioral) | 🔧 Polishing | — | $8K–$15K | $8K–$15K/seat; SIMPLIS switching model coverage via Xyce W-element |
| **HSPICE** | Synopsys | ltz (Xyce + libxycespice.so) | 🔵 Planned | Medium | $50K–$150K | Primary IC sign-off SPICE; `.sp` netlist compatibility via Xyce Hspice mode |
| **Spectre** | Cadence | ltz (Xyce + spectre translator) | 🔵 Planned | Medium | $80K–$200K | Translator pattern already established in Xyce upstream; `.scs` parser reuse |
| **Eldo / PowerSpice** | Siemens EDA | ltz (Xyce) | 🟡 Scoped | High | $50K–$120K | Lower priority; `.cir` netlist largely SPICE-compatible |
| **Spectre APS / XPS** | Cadence | ltz (Xyce + parallel smak) | 🟡 Scoped | High | $200K–$500K | Parallel Xyce via Wandering Threads / smak is the displacement path |

---

## Digital HDL Simulation

| Simulator | Vendor | Replacement | Status | Effort | Est. License Cost/Seat/Yr | Customer Savings Note |
|---|---|---|---|---|---|---|
| **VCS** | Synopsys | NVC (forked, LLVM-based) | 🔧 Active dev | High | $100K–$500K | Core Cameron EDA thesis; 100× performance target; correct X-propagation semantics |
| **Xcelium** | Cadence | NVC | 🔵 Planned | High | $100K–$400K | Shared displacement with VCS; NVC IEEE-2019 compliance is the wedge |
| **ModelSim / Questa** | Siemens EDA | NVC | 🔵 Planned | Medium | $20K–$80K | Large installed base in FPGAs / academia; NVC already passes most VHDL-2008 suites |
| **Riviera-PRO** | Aldec | NVC | 🟡 Scoped | Medium | $15K–$60K | Niche; VHDL-AMS support is differentiator |
| **Active-HDL** | Aldec | NVC | 🟡 Scoped | Low | $5K–$20K | Entry-level; NVC already exceeds on performance |
| **GHDL** | Open source | NVC (superset) | ✅ Done | — | Free | NVC is a strict superset; LLVM backend vs. GHDL's GCC backend gives 3–5× speedup |

---

## Mixed-Signal / AMS

| Simulator | Vendor | Replacement | Status | Effort | Est. License Cost/Seat/Yr | Customer Savings Note |
|---|---|---|---|---|---|---|
| **Virtuoso AMS / UltraSim** | Cadence | NVC + Xyce (VHPI/VPI co-sim) | 🔵 Planned | High | $200K–$600K | Highest-value displacement; AMS sign-off platform is Cameron EDA commercial upsell |
| **Questa ADMS** | Siemens EDA | NVC + Xyce | 🔵 Planned | High | $100K–$300K | Shares AMS co-sim architecture with Virtuoso path |
| **MATLAB/Simulink + Simscape** | MathWorks | NVC + Xyce + Mylex (ONNX→VerilogAMS) | 🔧 Active dev | High | $20K–$80K | Mylex NIR/ONNX→Verilog-AMS pipeline is the displacement path for Simulink behavioral models |
| **Simplorer / Twin Builder** | Ansys | NVC + Xyce + preCICE | 🟡 Scoped | High | $30K–$100K | Multi-domain (electrical + mechanical + thermal); preCICE coupling bus covers the co-sim |

---

## Power Electronics

| Simulator | Vendor | Replacement | Status | Effort | Est. License Cost/Seat/Yr | Customer Savings Note |
|---|---|---|---|---|---|---|
| **PSIM** | Powersim | ltz (Xyce + NVC control library) | 🔵 Planned | Medium | $3K–$12K | Switching converter focus; Xyce W-element + NVC PI/PWM control blocks cover core use cases |
| **PLECS** | Plexim | ltz (Xyce + NVC) | 🔵 Planned | Medium | $5K–$20K | Thermal + circuit co-sim; PACT covers thermal, Xyce covers circuit |
| **PSCAD / EMTDC** | Manitoba Hydro Int'l | ltz + pscad2vams translator | 🔧 Active dev | High | $10K–$30K | pscad2vams (.pscx → Xyce + Verilog-AMS); NREL/PyPSCAD models as test corpus; see PSCAD.md |

---

## Power Systems / Grid

| Simulator | Vendor | Replacement | Status | Effort | Est. License Cost/Seat/Yr | Customer Savings Note |
|---|---|---|---|---|---|---|
| **PSS/E** | Siemens EDA | cameron-grid (pandapower + NVC + Xyce) | 🔵 Planned | High | $30K–$60K | PSS/E RAW/.dyr import via pandapower; dominant transmission planning format |
| **ETAP** | ETAP / Operation Technology | cameron-grid | 🔵 Planned | High | $20K–$50K | CIM XML + Excel import; load flow, short circuit, arc flash coverage; see ETAP.md |
| **PowerWorld** | PowerWorld Corp | cameron-grid (pandapower) | 🟡 Scoped | Medium | $5K–$20K | pandapower reads PowerWorld .pwb via API; visualization layer deferred |
| **CYME** | Eaton | cameron-grid | 🟡 Scoped | Medium | $15K–$40K | Distribution focus; OpenDSS covers same space and reads CYME export |
| **PSCAD** | MHI | (see Power Electronics above) | 🔧 Active dev | High | $10K–$30K | — |
| **OpenDSS** | EPRI | cameron-grid (federate as peer) | ✅ Integrating | Low | Free | OpenDSS is open; cameron-grid federates it via preCICE rather than replacing |
| **HOMER Grid** | UL / HOMER Energy | cameron-grid + cvxpy MPC | 🟡 Scoped | Medium | $3K–$10K | Microgrid dispatch optimization; cvxpy on pandapower state is the displacement |

---

## DER Orchestration / DERMS / VPP

| Simulator | Vendor | Replacement | Status | Effort | Est. License Cost/Seat/Yr | Customer Savings Note |
|---|---|---|---|---|---|---|
| **AutoGrid Flex** | AutoGrid (Uplight) | cameron-grid + cvxpy MPC + LVRC | 🔵 Planned | High | $500K–$5M (utility SaaS contract) | Real-time pandapower state estimator + MPC optimizer replaces DERMS/VPP dispatch layer; LVRC provides sub-cycle autonomous response AutoGrid cannot match; homogeneous fleet (Prezent) eliminates device integration complexity |
| **EnergyHub DERMS** | EnergyHub | cameron-grid | 🟡 Scoped | High | $200K–$2M | Bring-your-own-device model; Prezent fleet homogeneity makes this moot |
| **Schneider EcoStruxure ADMS** | Schneider | cameron-grid (lower layer) | 🟡 Scoped | Very High | $1M–$10M | ADMS displacement is long-term; near-term play is API integration, not replacement |

---

## System-Level / Multi-Physics

| Simulator | Vendor | Replacement | Status | Effort | Est. License Cost/Seat/Yr | Customer Savings Note |
|---|---|---|---|---|---|---|
| **Dymola** | Dassault Systèmes | ltz (Modelica-subset → Verilog-A translator) | 🟡 Scoped | Very High | $20K–$60K | FMU read/export; ltz treats OpenModelica as market boundary per ltz architecture philosophy |
| **OpenModelica** | OSMC | ltz (FMU shim only) | 🟡 Scoped | Low | Free | LD_PRELOAD shim on libOpenModelicaRuntime.so; upstream collaboration explicitly avoided |
| **PACT** | Boston Univ / Brown | cameron-grid (federate as peer) | ✅ Integrating | Low | Free | Open-source thermal; federated with per-die Xyce instances; not a replacement target |

---

## Status Key

| Symbol | Meaning |
|---|---|
| ✅ Done / Integrating | Working displacement or federation in place |
| 🔧 Active dev / Polishing | In active development sprint |
| 🔵 Planned | Scoped, not yet started |
| 🟡 Scoped | Identified, effort estimated, not yet prioritized |

---

## Cumulative Customer Savings Estimate

Rough per-customer annual savings for a mid-size utility or IC design house adopting the full open stack:

| Customer Type | Displaced Tools | Est. Annual Savings |
|---|---|---|
| IC design team (10 seats) | HSPICE + Spectre + VCS/Xcelium + Questa ADMS | $3M–$8M/yr |
| Power electronics team (5 seats) | PSCAD + PSIM + PLECS + MATLAB/Simulink | $150K–$400K/yr |
| Utility / grid operator | ETAP + PSS/E + AutoGrid SaaS | $600K–$6M/yr |
| EV fleet operator (Prezent-scale) | AutoGrid Flex + HOMER | $500K–$5M/yr |
