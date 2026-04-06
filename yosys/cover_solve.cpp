// SAT-based coverage closure using Yosys RTLIL + Z3
//
// Reads RTL via libyosys, builds Z3 bitvector model of the transition
// relation, unrolls K cycles, and solves for input sequences that reach
// specified coverage targets (signal values, FSM states, branch conditions).
//
// Usage: cover_solve <input.sv> <top_module> [--depth N] [--target signal=value] ...

#include <kernel/yosys.h>
#include <kernel/rtlil.h>
#include <kernel/sigtools.h>
#include <z3++.h>
#include <cstdio>
#include <string>
#include <map>
#include <vector>
#include <algorithm>

using namespace Yosys;

// Sanitize names for Z3 (same as gen_statemachine)
static std::string zname(const std::string &s) {
    std::string r;
    for (char c : s) {
        if (c == '\\' || c == '$' || c == '.' || c == '/')
            r += '_';
        else if (c == '[') r += '_';
        else if (c == ']') continue;
        else r += c;
    }
    if (!r.empty() && (r[0] >= '0' && r[0] <= '9'))
        r = "w_" + r;
    return r;
}

// Per-cycle signal representation
struct CycleVars {
    std::map<std::string, z3::expr> wires;   // wire_name -> bitvector expr
    std::map<std::string, z3::expr> regs;    // reg_name -> bitvector expr (state)
    std::map<std::string, z3::expr> inputs;  // input_name -> bitvector expr
};

int main(int argc, char **argv)
{
    // Parse arguments
    const char *verilog_file = nullptr;
    const char *top_name = nullptr;
    int depth = 15;
    std::vector<std::pair<std::string, uint64_t>> targets;  // signal=value pairs

    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        if (arg == "--depth" && i+1 < argc) {
            depth = atoi(argv[++i]);
        } else if (arg.substr(0, 9) == "--target=") {
            auto eq = arg.find('=', 9);
            if (eq != std::string::npos) {
                std::string sig = arg.substr(9, eq - 9);
                uint64_t val = strtoull(arg.substr(eq+1).c_str(), nullptr, 0);
                targets.push_back({sig, val});
            }
        } else if (!verilog_file) {
            verilog_file = argv[i];
        } else if (!top_name) {
            top_name = argv[i];
        }
    }

    if (!verilog_file || !top_name) {
        fprintf(stderr, "Usage: cover_solve <input.sv> <top> [--depth N] [--target=sig=val ...]\n");
        return 1;
    }

    fprintf(stderr, "cover_solve: %s top=%s depth=%d targets=%zu\n",
            verilog_file, top_name, depth, targets.size());

    // --- Yosys frontend (same as gen_statemachine) ---
    Yosys::yosys_setup();

    std::string sv_flag = "";
    std::string vf(verilog_file);
    if (vf.size() >= 3 && vf.substr(vf.size()-3) == ".sv")
        sv_flag = " -sv";
    Yosys::run_pass(std::string("read_verilog") + sv_flag + " " + verilog_file);
    Yosys::run_pass(std::string("hierarchy -top ") + top_name);
    Yosys::run_pass("proc");
    Yosys::run_pass("flatten");
    Yosys::run_pass("opt");

    auto *design = Yosys::yosys_get_design();
    auto *mod = design->top_module();
    SigMap sigmap(mod);

    // Classify cells (same as gen_statemachine)
    struct RegInfo {
        std::string name;
        int width;
        uint64_t arst_val;
        // D input will be expressed as Z3 formula
        RTLIL::SigSpec d_sig;
        RTLIL::SigSpec en_sig;  // empty = always enabled
        bool en_polarity = true;
    };

    std::vector<RegInfo> registers;
    std::vector<RTLIL::Cell*> comb_cells;
    std::set<std::string> reg_names_used;

    for (auto &c : mod->cells_) {
        auto *cell = c.second;
        auto type = cell->type.str();
        if (type == "$scopeinfo" || type == "$meminit" || type == "$meminit_v2") continue;

        bool is_reg = (type == "$adff" || type == "$dff" || type == "$adffe"
                       || type == "$dffe" || type == "$sdff" || type == "$sdffe");
        if (is_reg) {
            RegInfo reg;
            auto &q = cell->getPort(ID::Q);
            std::string wname = zname((*q.chunks().begin()).wire->name.str());
            if (reg_names_used.count(wname))
                wname = zname(cell->name.str());
            reg_names_used.insert(wname);
            reg.name = wname;
            reg.width = q.size();
            reg.d_sig = cell->getPort(ID::D);
            reg.arst_val = 0;
            if (type == "$adff" || type == "$adffe") {
                auto arst_val = cell->getParam(ID(ARST_VALUE));
                uint64_t rv = 0;
                for (int i = arst_val.size()-1; i >= 0; i--)
                    rv = (rv << 1) | (arst_val[i] == RTLIL::S1 ? 1 : 0);
                reg.arst_val = rv;
            }
            if (type == "$dffe" || type == "$adffe" || type == "$sdffe") {
                if (cell->hasPort(ID::EN)) {
                    reg.en_sig = cell->getPort(ID::EN);
                    reg.en_polarity = !cell->hasParam(ID(EN_POLARITY)) ||
                                      cell->getParam(ID(EN_POLARITY)).as_bool();
                }
            }
            registers.push_back(reg);
        } else {
            comb_cells.push_back(cell);
        }
    }

    // Topological sort of combinational cells
    std::map<RTLIL::Wire*, RTLIL::Cell*> wire_driver;
    for (auto *cell : comb_cells) {
        for (auto port_id : {ID::Y, ID(DATA)}) {
            if (cell->hasPort(port_id)) {
                for (auto &chunk : cell->getPort(port_id).chunks())
                    if (chunk.wire) wire_driver[chunk.wire] = cell;
            }
        }
    }
    std::set<RTLIL::Cell*> visited;
    std::vector<RTLIL::Cell*> sorted;
    std::function<void(RTLIL::Cell*)> topo_visit;
    topo_visit = [&](RTLIL::Cell *cell) {
        if (visited.count(cell)) return;
        visited.insert(cell);
        for (auto &conn : cell->connections()) {
            if (conn.first == ID::Y) continue;
            for (auto &chunk : conn.second.chunks())
                if (chunk.wire && wire_driver.count(chunk.wire))
                    topo_visit(wire_driver[chunk.wire]);
        }
        sorted.push_back(cell);
    };
    for (auto *cell : comb_cells) topo_visit(cell);

    fprintf(stderr, "  %zu registers, %zu comb cells\n", registers.size(), sorted.size());

    // --- Z3 model ---
    z3::context ctx;
    z3::solver solver(ctx);

    // Helper: get or create a Z3 bitvector for a SigSpec at a given cycle
    auto make_cycle_prefix = [](int cycle, const std::string &name) -> std::string {
        return "c" + std::to_string(cycle) + "_" + name;
    };

    // Map wire names to widths
    std::map<std::string, int> wire_widths;
    for (auto &w : mod->wires_)
        wire_widths[zname(w.second->name.str())] = w.second->width;
    for (auto &reg : registers)
        wire_widths[reg.name] = reg.width;

    // For each cycle, we need Z3 variables for all inputs and register states
    // sig_expr equivalent for Z3
    // Use expr_vector per cycle keyed by name (avoid default-constructing z3::expr)
    std::map<std::string, z3::expr> all_vars;  // "cN_name" -> expr

    auto get_var = [&](int cycle, const std::string &name, int width) -> z3::expr {
        std::string full = make_cycle_prefix(cycle, name);
        auto it = all_vars.find(full);
        if (it != all_vars.end()) return it->second;
        z3::expr v = ctx.bv_const(full.c_str(), width);
        all_vars.insert({full, v});
        return v;
    };

    // Convert a SigSpec to Z3 expression at a given cycle
    std::function<z3::expr(const SigSpec&, int)> sig_to_z3;
    sig_to_z3 = [&](const SigSpec &sig, int cycle) -> z3::expr {
        auto mapped = sigmap(sig);
        if (mapped.size() == 0)
            return ctx.bv_val(0, 1);  // degenerate: return 1-bit zero
        if (mapped.is_fully_const()) {
            auto val = mapped.as_const();
            uint64_t v = 0;
            for (int i = val.size()-1; i >= 0; i--)
                v = (v << 1) | (val[i] == RTLIL::S1 ? 1 : 0);
            return ctx.bv_val(v, mapped.size());
        }
        if (mapped.chunks().size() == 1) {
            auto &chunk = *mapped.chunks().begin();
            if (chunk.wire) {
                std::string wn = zname(chunk.wire->name.str());
                int ww = chunk.wire->width;
                z3::expr base = get_var(cycle, wn, ww);
                if (chunk.offset == 0 && chunk.width == ww)
                    return base;
                return base.extract(chunk.offset + chunk.width - 1, chunk.offset);
            }
        }
        // Multi-chunk: concatenate
        z3::expr result = ctx.bv_val(0, 0);
        bool first = true;
        // Chunks are LSB-first in Yosys
        for (auto &chunk : mapped.chunks()) {
            z3::expr part = ctx.bv_val(0, chunk.width);
            if (chunk.wire) {
                std::string wn = zname(chunk.wire->name.str());
                z3::expr base = get_var(cycle, wn, chunk.wire->width);
                if (chunk.offset == 0 && chunk.width == chunk.wire->width)
                    part = base;
                else
                    part = base.extract(chunk.offset + chunk.width - 1, chunk.offset);
            } else {
                uint64_t v = 0;
                for (int i = chunk.data.size()-1; i >= 0; i--)
                    v = (v << 1) | (chunk.data[i] == RTLIL::S1 ? 1 : 0);
                part = ctx.bv_val(v, chunk.width);
            }
            if (first) { result = part; first = false; }
            else result = z3::concat(part, result);  // MSB concat LSB
        }
        return result;
    };

    auto is_signed = [](RTLIL::Cell *cell) -> bool {
        return cell->hasParam(ID(A_SIGNED)) && cell->getParam(ID(A_SIGNED)).as_bool();
    };

    // Build constraints for one cycle
    auto build_cycle = [&](int cycle) {
        // Combinational logic
        for (auto *cell : sorted) {
            auto type = cell->type.str();
            if (!cell->hasPort(ID::Y)) continue;
            auto &y_port = cell->getPort(ID::Y);
            if (y_port.chunks().begin() == y_port.chunks().end()) continue;
            if (!(*y_port.chunks().begin()).wire) continue;

            std::string y_name = zname((*y_port.chunks().begin()).wire->name.str());
            int y_width = y_port.size();
            z3::expr y = get_var(cycle, y_name, y_width);

            try {
                if (y_width == 0) continue;  // skip zero-width signals
                z3::expr constraint = ctx.bool_val(true);
                bool have_constraint = false;

                // Helper: normalize two operands to the same width
                auto norm2 = [&](z3::expr &a, z3::expr &b, int target_w) {
                    unsigned aw = a.get_sort().bv_size();
                    unsigned bw = b.get_sort().bv_size();
                    unsigned tw = (unsigned)target_w;
                    if (aw < tw) a = z3::zext(a, tw - aw);
                    else if (aw > tw) a = a.extract(tw - 1, 0);
                    if (bw < tw) b = z3::zext(b, tw - bw);
                    else if (bw > tw) b = b.extract(tw - 1, 0);
                };
                auto norm1 = [&](z3::expr &a, int target_w) {
                    unsigned aw = a.get_sort().bv_size();
                    unsigned tw = (unsigned)target_w;
                    if (aw < tw) a = z3::zext(a, tw - aw);
                    else if (aw > tw) a = a.extract(tw - 1, 0);
                };

                if (type == "$add") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto b = sig_to_z3(cell->getPort(ID::B), cycle);
                    norm2(a, b, y_width);
                    constraint = (y == (a + b)); have_constraint = true;
                } else if (type == "$sub") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto b = sig_to_z3(cell->getPort(ID::B), cycle);
                    norm2(a, b, y_width);
                    constraint = (y == (a - b)); have_constraint = true;
                } else if (type == "$and") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto b = sig_to_z3(cell->getPort(ID::B), cycle);
                    norm2(a, b, y_width);
                    constraint = (y == (a & b)); have_constraint = true;
                } else if (type == "$or") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto b = sig_to_z3(cell->getPort(ID::B), cycle);
                    norm2(a, b, y_width);
                    constraint = (y == (a | b)); have_constraint = true;
                } else if (type == "$xor") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto b = sig_to_z3(cell->getPort(ID::B), cycle);
                    norm2(a, b, y_width);
                    constraint = (y == (a ^ b)); have_constraint = true;
                } else if (type == "$not") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    norm1(a, y_width);
                    constraint = (y == ~a); have_constraint = true;
                } else if (type == "$eq") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto b = sig_to_z3(cell->getPort(ID::B), cycle);
                    int max_w = std::max(a.get_sort().bv_size(), b.get_sort().bv_size());
                    norm2(a, b, max_w);
                    z3::expr cmp = z3::ite(a == b, ctx.bv_val(1, y_width), ctx.bv_val(0, y_width));
                    constraint = (y == cmp); have_constraint = true;
                } else if (type == "$ne") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto b = sig_to_z3(cell->getPort(ID::B), cycle);
                    int max_w = std::max(a.get_sort().bv_size(), b.get_sort().bv_size());
                    norm2(a, b, max_w);
                    z3::expr cmp = z3::ite(a != b, ctx.bv_val(1, y_width), ctx.bv_val(0, y_width));
                    constraint = (y == cmp); have_constraint = true;
                } else if (type == "$lt") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto b = sig_to_z3(cell->getPort(ID::B), cycle);
                    int max_w = std::max(a.get_sort().bv_size(), b.get_sort().bv_size());
                    norm2(a, b, max_w);
                    z3::expr cmp = is_signed(cell)
                        ? z3::ite(a < b, ctx.bv_val(1, y_width), ctx.bv_val(0, y_width))
                        : z3::ite(z3::ult(a, b), ctx.bv_val(1, y_width), ctx.bv_val(0, y_width));
                    constraint = (y == cmp); have_constraint = true;
                } else if (type == "$mux") {
                    auto s = sig_to_z3(cell->getPort(ID::S), cycle);
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto b = sig_to_z3(cell->getPort(ID::B), cycle);
                    norm2(a, b, y_width);
                    z3::expr sel = z3::ite(s == ctx.bv_val(1, s.get_sort().bv_size()), b, a);
                    constraint = (y == sel); have_constraint = true;
                } else if (type == "$pmux") {
                    auto a_expr = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto &b_sig = cell->getPort(ID::B);
                    auto &s_sig = cell->getPort(ID::S);
                    int n_cases = s_sig.size();
                    z3::expr result = a_expr;  // default
                    for (int i = 0; i < n_cases; i++) {
                        auto s_bit = sig_to_z3(s_sig.extract(i, 1), cycle);
                        auto b_slice = sig_to_z3(b_sig.extract(i * y_width, y_width), cycle);
                        result = z3::ite(s_bit == ctx.bv_val(1, 1), b_slice, result);
                    }
                    constraint = (y == result); have_constraint = true;
                } else if (type == "$logic_not") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    z3::expr zero = ctx.bv_val(0, a.get_sort().bv_size());
                    constraint = (y == z3::ite(a == zero, ctx.bv_val(1, y_width), ctx.bv_val(0, y_width)));
                    have_constraint = true;
                } else if (type == "$reduce_or" || type == "$reduce_bool") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    z3::expr zero = ctx.bv_val(0, a.get_sort().bv_size());
                    constraint = (y == z3::ite(a != zero, ctx.bv_val(1, y_width), ctx.bv_val(0, y_width)));
                    have_constraint = true;
                } else if (type == "$reduce_and") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    uint64_t all_ones = a.get_sort().bv_size() >= 64 ? ~0ULL : ((1ULL << a.get_sort().bv_size()) - 1);
                    z3::expr full = ctx.bv_val(all_ones, a.get_sort().bv_size());
                    constraint = (y == z3::ite(a == full, ctx.bv_val(1, y_width), ctx.bv_val(0, y_width)));
                    have_constraint = true;
                } else if (type == "$shl") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto b = sig_to_z3(cell->getPort(ID::B), cycle);
                    norm2(a, b, y_width);
                    constraint = (y == z3::shl(a, b)); have_constraint = true;
                } else if (type == "$shr") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto b = sig_to_z3(cell->getPort(ID::B), cycle);
                    norm1(a, y_width); norm1(b, y_width);
                    constraint = (y == z3::lshr(a, b)); have_constraint = true;
                } else if (type == "$mul") {
                    auto a = sig_to_z3(cell->getPort(ID::A), cycle);
                    auto b = sig_to_z3(cell->getPort(ID::B), cycle);
                    norm2(a, b, y_width);
                    constraint = (y == (a * b)); have_constraint = true;
                }

                if (have_constraint)
                    solver.add(constraint);
            } catch (z3::exception &e) {
                // Skip cells we can't encode
            }
        }
    };

    // --- Unroll and solve ---

    // Set initial state (cycle 0 = reset values)
    for (auto &reg : registers) {
        z3::expr r0 = get_var(0, reg.name, reg.width);
        solver.add(r0 == ctx.bv_val(reg.arst_val, reg.width));
    }

    // Constrain rst_ni: cycle 0 = 0 (reset), cycle 1+ = 1
    // Find the reset signal
    for (auto &w : mod->wires_) {
        std::string wn = zname(w.second->name.str());
        if (wn.find("rst") != std::string::npos && w.second->port_input) {
            solver.add(get_var(0, wn, w.second->width) == ctx.bv_val(0, w.second->width));
            for (int c = 1; c <= depth; c++)
                solver.add(get_var(c, wn, w.second->width) == ctx.bv_val(1, w.second->width));
            fprintf(stderr, "  reset signal: %s\n", wn.c_str());
            break;
        }
    }

    // Build transition relation for each cycle
    for (int c = 0; c < depth; c++) {
        fprintf(stderr, "  building cycle %d/%d...\r", c+1, depth);
        build_cycle(c);

        // Register updates: reg[c+1] = D[c] (with enable gating)
        for (auto &reg : registers) {
            z3::expr next = get_var(c+1, reg.name, reg.width);
            z3::expr d = sig_to_z3(reg.d_sig, c);
            z3::expr curr = get_var(c, reg.name, reg.width);

            if (d.get_sort().bv_size() != (unsigned)reg.width) {
                if (d.get_sort().bv_size() > (unsigned)reg.width)
                    d = d.extract(reg.width - 1, 0);
                else
                    d = z3::zext(d, reg.width - d.get_sort().bv_size());
            }

            if (reg.en_sig.size() > 0) {
                z3::expr en = sig_to_z3(reg.en_sig, c);
                z3::expr one = ctx.bv_val(1, en.get_sort().bv_size());
                if (reg.en_polarity)
                    solver.add(next == z3::ite(en == one, d, curr));
                else
                    solver.add(next == z3::ite(en == one, curr, d));
            } else {
                solver.add(next == d);
            }
        }
    }
    fprintf(stderr, "\n");

    // If no explicit targets, auto-detect from FSM registers
    if (targets.empty()) {
        for (auto &reg : registers) {
            if (reg.width >= 2 && reg.width <= 6 &&
                (reg.name.find("state") != std::string::npos ||
                 reg.name.find("fsm") != std::string::npos)) {
                // Add all possible states as targets
                for (int s = 0; s < (1 << reg.width); s++)
                    targets.push_back({reg.name, (uint64_t)s});
                fprintf(stderr, "  auto-target: FSM %s (%d states)\n",
                        reg.name.c_str(), 1 << reg.width);
            }
        }
    }

    // Solve for each target
    int solved = 0, unsat = 0, errors = 0;
    for (size_t ti = 0; ti < targets.size(); ti++) {
        auto &sig = targets[ti].first;
        auto &val = targets[ti].second;
        // Find the signal width
        int width = 0;
        for (auto &reg : registers)
            if (reg.name == sig) { width = reg.width; break; }
        if (width == 0) {
            auto it = wire_widths.find(sig);
            if (it != wire_widths.end()) width = it->second;
        }
        if (width == 0) {
            fprintf(stderr, "  target %s: signal not found\n", sig.c_str());
            errors++;
            continue;
        }

        // Try to reach this value at any cycle 1..depth
        solver.push();
        z3::expr_vector reach_any(ctx);
        for (int c = 1; c <= depth; c++) {
            z3::expr v = get_var(c, sig, width);
            reach_any.push_back(v == ctx.bv_val(val, width));
        }
        solver.add(z3::mk_or(reach_any));

        auto result = solver.check();
        if (result == z3::sat) {
            auto model = solver.get_model();
            // Find which cycle achieved the target
            for (int c = 1; c <= depth; c++) {
                z3::expr v = get_var(c, sig, width);
                z3::expr eval = model.eval(v);
                uint64_t got = eval.is_numeral() ? eval.get_numeral_uint64() : 0;
                if (got == val) {
                    printf("REACHABLE: %s=%lu at cycle %d\n", sig.c_str(), val, c);
                    // Print input sequence for primary inputs
                    printf("  inputs:\n");
                    for (auto &w : mod->wires_) {
                        auto *wire = w.second;
                        if (wire->port_input) {
                            std::string wn = zname(wire->name.str());
                            if (wn.find("clk") != std::string::npos) continue;
                            if (wn.find("rst") != std::string::npos) continue;
                            for (int ic = 0; ic <= c; ic++) {
                                z3::expr inp = model.eval(get_var(ic, wn, wire->width));
                                if (inp.is_numeral())
                                    printf("    cycle %d: %s = 0x%llx\n", ic, wn.c_str(),
                                           (unsigned long long)inp.get_numeral_uint64());
                            }
                        }
                    }
                    solved++;
                    break;
                }
            }
        } else if (result == z3::unsat) {
            printf("UNREACHABLE: %s=%lu (in %d cycles)\n", sig.c_str(), val, depth);
            unsat++;
        } else {
            printf("UNKNOWN: %s=%lu\n", sig.c_str(), val);
            errors++;
        }
        solver.pop();
    }

    printf("\n=== Coverage Solve Summary ===\n");
    printf("Targets: %zu  Reachable: %d  Unreachable: %d  Errors: %d\n",
           targets.size(), solved, unsat, errors);

    Yosys::yosys_shutdown();
    return 0;
}
