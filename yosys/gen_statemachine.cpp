// Generate cycle-based state machine C code from Yosys RTLIL
//
// Reads Verilog via libyosys, flattens to RTLIL cells,
// topologically sorts the combinational logic, and emits C code
// that evaluates the entire design in one function call per clock cycle.

#include <kernel/yosys.h>
#include <kernel/rtlil.h>
#include <kernel/sigtools.h>
#include <cstdio>
#include <string>
#include <map>
#include <set>
#include <vector>
#include <algorithm>
#include <sstream>

using namespace Yosys;

// Sanitize RTLIL names to valid C identifiers
static std::string cname(const std::string &s) {
    std::string r;
    for (char c : s) {
        if (c == '\\' || c == '$' || c == '.' || c == '/' || c == ':')
            r += '_';
        else if (c == '[') r += '_';
        else if (c == ']') continue;
        else r += c;
    }
    // Prefix if starts with digit
    if (!r.empty() && (r[0] >= '0' && r[0] <= '9'))
        r = "w_" + r;
    return r;
}

// Get a C expression for a SigSpec (wire reference or constant)
static std::string sig_expr(const SigSpec &sig, const SigMap &sigmap) {
    auto mapped = sigmap(sig);
    if (mapped.is_fully_const()) {
        auto val = mapped.as_const();
        uint64_t v = 0;
        for (int i = val.bits().size()-1; i >= 0; i--)
            v = (v << 1) | (val[i] == RTLIL::S1 ? 1 : 0);
        std::ostringstream oss;
        oss << "UINT64_C(0x" << std::hex << v << ")";
        return oss.str();
    }

    // Single wire reference
    if (mapped.chunks().size() == 1) {
        auto &chunk = (*mapped.chunks().begin());
        if (chunk.wire) {
            std::string wn = cname(chunk.wire->name.str());
            if (chunk.offset == 0 && chunk.width == chunk.wire->width)
                return wn;
            else if (chunk.width == 1)
                return "(((" + wn + ") >> " + std::to_string(chunk.offset) + ") & 1)";
            else {
                uint64_t mask = (1ULL << chunk.width) - 1;
                std::ostringstream oss;
                oss << "(((" << wn << ") >> " << chunk.offset << ") & UINT64_C(0x" << std::hex << mask << "))";
                return oss.str();
            }
        }
    }

    // Multi-chunk: build by concatenation
    std::string expr = "0";
    int pos = 0;
    for (auto &chunk : mapped.chunks()) {
        std::string part;
        if (chunk.wire) {
            std::string wn = cname(chunk.wire->name.str());
            if (chunk.offset == 0 && chunk.width == chunk.wire->width)
                part = wn;
            else {
                uint64_t mask = (1ULL << chunk.width) - 1;
                std::ostringstream oss;
                oss << "((" << wn << " >> " << chunk.offset << ") & UINT64_C(0x" << std::hex << mask << "))";
                part = oss.str();
            }
        } else {
            uint64_t v = 0;
            for (int i = chunk.data.size()-1; i >= 0; i--)
                v = (v << 1) | (chunk.data[i] == RTLIL::S1 ? 1 : 0);
            std::ostringstream oss;
            oss << "UINT64_C(0x" << std::hex << v << ")";
            part = oss.str();
        }
        if (pos == 0)
            expr = part;
        else {
            std::ostringstream oss;
            oss << "((" << part << " << " << std::dec << pos << ") | " << expr << ")";
            expr = oss.str();
        }
        pos += chunk.width;
    }
    return expr;
}

// Check if cell has signed operands
static bool is_signed(RTLIL::Cell *cell) {
    return cell->hasParam(ID(A_SIGNED)) &&
           cell->getParam(ID(A_SIGNED)).as_bool();
}

// Generate sign-extension expression for comparison operands
static std::string signed_expr(const std::string &expr, int width) {
    if (width >= 64) return "(int64_t)" + expr;
    std::ostringstream oss;
    oss << "((int64_t)((" << expr << ") << " << (64 - width) << ") >> " << (64 - width) << ")";
    return oss.str();
}

// Build the cache path for a module:
//   ~/.cache/nvc/accel/accel-mod_<module>-arch_from_verilog.so
static std::string accel_cache_path(const char *module_name, const char *ext) {
    const char *home = getenv("HOME");
    if (!home) home = "/tmp";
    std::string path = std::string(home) + "/.cache/nvc/accel/accel-mod_"
        + module_name + "-arch_from_verilog" + ext;
    // Lowercase the module name portion
    return path;
}

int main(int argc, char **argv)
{
    fprintf(stderr, "gen_statemachine starting...\n");
    const char *verilog_file = argc > 1 ? argv[1] : "/tmp/rtl_design.v";
    const char *top_name = argc > 2 ? argv[2] : "rtl_top";
    const char *output_file = argc > 3 ? argv[3] : NULL;
    fprintf(stderr, "  input: %s  top: %s\n", verilog_file, top_name);

    // Default output: ~/.cache/nvc/accel/accel-mod_<top>.c
    std::string default_output;
    if (!output_file) {
        default_output = accel_cache_path(top_name, ".c");
        output_file = default_output.c_str();
    }

    Yosys::yosys_setup();

    // Use -sv flag for .sv files
    std::string sv_flag = "";
    std::string vf(verilog_file);
    if (vf.size() >= 3 && vf.substr(vf.size()-3) == ".sv")
        sv_flag = " -sv";
    std::string cmd = std::string("read_verilog") + sv_flag + " " + verilog_file;
    Yosys::run_pass(cmd);
    Yosys::run_pass(std::string("hierarchy -top ") + top_name);
    Yosys::run_pass("proc");
    Yosys::run_pass("flatten");
    Yosys::run_pass("opt");

    auto *design = Yosys::yosys_get_design();
    auto *mod = design->top_module();
    SigMap sigmap(mod);

    // Collect all wires, identify registers
    struct RegInfo {
        std::string name;
        std::string d_expr;
        std::string en_expr;  // empty = always enabled
        std::string src;      // source location "file:line.col"
        int width;
        uint64_t arst_val;
    };
    struct MemInfo {
        std::string name;
        int width;          // bits per word
        int depth;          // number of words
        int abits;          // address bits
        std::map<int, uint64_t> init;  // addr -> value
    };
    std::map<std::string, MemInfo> memories;  // keyed by MEMID

    std::vector<RegInfo> registers;
    std::vector<RTLIL::Cell*> comb_cells;

    // First pass: collect memory info from $meminit cells
    for (auto &c : mod->cells_) {
        auto *cell = c.second;
        auto type = cell->type.str();
        if (type == "$meminit" || type == "$meminit_v2") {
            std::string memid = cell->getParam(ID(MEMID)).decode_string();
            int width = cell->getParam(ID(WIDTH)).as_int();
            int abits = cell->getParam(ID(ABITS)).as_int();
            auto &addr_sig = cell->getPort(ID(ADDR));
            auto &data_sig = cell->getPort(ID(DATA));

            auto &mem = memories[memid];
            mem.name = cname(memid);
            mem.width = width;
            mem.abits = abits;

            // Get address and data constants
            auto addr_const = sigmap(addr_sig).as_const();
            auto data_const = sigmap(data_sig).as_const();
            uint64_t addr = 0;
            for (int i = addr_const.size()-1; i >= 0; i--)
                addr = (addr << 1) | (addr_const[i] == RTLIL::S1 ? 1 : 0);
            uint64_t data = 0;
            for (int i = data_const.size()-1; i >= 0; i--)
                data = (data << 1) | (data_const[i] == RTLIL::S1 ? 1 : 0);
            mem.init[addr] = data;
            int words = cell->getParam(ID(WORDS)).as_int();
            if ((int)addr + words > mem.depth)
                mem.depth = addr + words;
        }
    }

    // Second pass: get depth from $memrd cells if not set
    for (auto &c : mod->cells_) {
        auto *cell = c.second;
        auto type = cell->type.str();
        if (type == "$memrd" || type == "$memrd_v2") {
            std::string memid = cell->getParam(ID(MEMID)).decode_string();
            auto &mem = memories[memid];
            if (mem.depth == 0)
                mem.depth = 1 << cell->getParam(ID(ABITS)).as_int();
            mem.width = cell->getParam(ID(WIDTH)).as_int();
            mem.abits = cell->getParam(ID(ABITS)).as_int();
            mem.name = cname(memid);
        }
    }

    std::set<std::string> reg_names_used;

    // Main pass: classify cells
    for (auto &c : mod->cells_) {
        auto *cell = c.second;
        auto type = cell->type.str();

        if (type == "$scopeinfo") continue;
        if (type == "$meminit" || type == "$meminit_v2") continue;  // already handled

        bool is_reg = (type == "$adff" || type == "$dff" || type == "$adffe"
                       || type == "$dffe" || type == "$sdff" || type == "$sdffe");
        if (is_reg) {
            RegInfo reg;
            auto &q = cell->getPort(ID::Q);
            auto &d = cell->getPort(ID::D);
            // Use wire name, but fall back to cell name on collision
            std::string wname = cname((*q.chunks().begin()).wire->name.str());
            if (reg_names_used.count(wname))
                wname = cname(cell->name.str());
            reg_names_used.insert(wname);
            reg.name = wname;
            reg.d_expr = sig_expr(d, sigmap);
            reg.width = q.size();
            reg.src = cell->get_src_attribute();

            // Get async reset value if present
            reg.arst_val = 0;
            if (type == "$adff" || type == "$adffe") {
                auto arst_val = cell->getParam(ID(ARST_VALUE));
                uint64_t rv = 0;
                for (int i = arst_val.size()-1; i >= 0; i--)
                    rv = (rv << 1) | (arst_val[i] == RTLIL::S1 ? 1 : 0);
                reg.arst_val = rv;
            }

            // Get clock enable if present
            if (type == "$dffe" || type == "$adffe" || type == "$sdffe") {
                if (cell->hasPort(ID::EN)) {
                    reg.en_expr = sig_expr(cell->getPort(ID::EN), sigmap);
                    // Check enable polarity
                    if (cell->hasParam(ID(EN_POLARITY)) &&
                        !cell->getParam(ID(EN_POLARITY)).as_bool())
                        reg.en_expr = "(!" + reg.en_expr + ")";
                }
            }

            registers.push_back(reg);
        } else {
            comb_cells.push_back(cell);
        }
    }

    // --- FSM detection ---
    // A register is likely an FSM state variable if:
    //   1. Width <= 8 bits (at most 256 states)
    //   2. Its Q output drives the select (S) port of a $pmux or $mux cell
    // We also accept registers whose name contains "state" or "fsm".
    std::set<RTLIL::Wire*> mux_select_wires;
    for (auto *cell : comb_cells) {
        auto type = cell->type.str();
        if (type == "$pmux" || type == "$mux") {
            if (cell->hasPort(ID::S)) {
                for (auto &chunk : cell->getPort(ID::S).chunks())
                    if (chunk.wire) mux_select_wires.insert(chunk.wire);
            }
        }
    }

    struct FsmInfo {
        size_t reg_idx;         // index into registers[]
        std::string name;
        int width;
        int max_states;         // 1 << width
    };
    std::vector<FsmInfo> fsms;

    for (size_t i = 0; i < registers.size(); i++) {
        auto &reg = registers[i];
        if (reg.width < 2 || reg.width > 6) continue;  // FSMs are 2-6 bits (4-64 states)

        bool is_mux_sel = false;
        // Check if this register's Q wire feeds a mux select
        for (auto &c : mod->cells_) {
            auto *cell = c.second;
            auto type = cell->type.str();
            bool is_this_reg = (type == "$adff" || type == "$dff" || type == "$adffe"
                                || type == "$dffe" || type == "$sdff" || type == "$sdffe");
            if (is_this_reg && cell->hasPort(ID::Q)) {
                auto &q = cell->getPort(ID::Q);
                if (q.chunks().begin() != q.chunks().end() && (*q.chunks().begin()).wire) {
                    std::string wn = cname((*q.chunks().begin()).wire->name.str());
                    if (wn == reg.name) {
                        // Check if the Q wire is in our mux select set
                        for (auto &chunk : q.chunks())
                            if (chunk.wire && mux_select_wires.count(chunk.wire))
                                is_mux_sel = true;
                    }
                }
            }
        }

        // Also accept by name pattern
        bool name_match = (reg.name.find("state") != std::string::npos ||
                           reg.name.find("fsm") != std::string::npos ||
                           reg.name.find("_st") != std::string::npos);

        if (is_mux_sel || name_match) {
            FsmInfo fsm;
            fsm.reg_idx = i;
            fsm.name = reg.name;
            fsm.width = reg.width;
            fsm.max_states = 1 << reg.width;
            fsms.push_back(fsm);
            fprintf(stderr, "FSM detected: %s (%d bits, %d max states)%s%s\n",
                    reg.name.c_str(), reg.width, fsm.max_states,
                    is_mux_sel ? " [mux-select]" : "",
                    name_match ? " [name-match]" : "");
        }
    }

    // Build dependency graph for topological sort
    // Map output wire -> cell that produces it
    std::map<RTLIL::Wire*, RTLIL::Cell*> wire_driver;
    for (auto *cell : comb_cells) {
        // Check Y port (most cells) and DATA port ($memrd)
        for (auto port_id : {ID::Y, ID(DATA)}) {
            if (cell->hasPort(port_id)) {
                auto &y = cell->getPort(port_id);
                for (auto &chunk : y.chunks())
                    if (chunk.wire) wire_driver[chunk.wire] = cell;
            }
        }
    }

    // Topological sort
    std::set<RTLIL::Cell*> visited;
    std::vector<RTLIL::Cell*> sorted;
    std::function<void(RTLIL::Cell*)> topo_visit;
    topo_visit = [&](RTLIL::Cell *cell) {
        if (visited.count(cell)) return;
        visited.insert(cell);
        // Visit dependencies (input wires)
        for (auto &conn : cell->connections()) {
            if (conn.first == ID::Y) continue;  // skip output
            for (auto &chunk : conn.second.chunks()) {
                if (chunk.wire && wire_driver.count(chunk.wire))
                    topo_visit(wire_driver[chunk.wire]);
            }
        }
        sorted.push_back(cell);
    };
    for (auto *cell : comb_cells)
        topo_visit(cell);

    // Helper: emit #line directive from Yosys src attribute
    // Format: "filename:line.col-line.col" -> #line <line> "filename"
    auto emit_line_directive = [](FILE *f, RTLIL::Cell *cell) {
        auto src = cell->get_src_attribute();
        if (src.empty()) return;
        // Parse "filename:line.col..."
        auto colon = src.rfind(':');
        if (colon == std::string::npos) return;
        std::string file = src.substr(0, colon);
        int line = 0;
        sscanf(src.c_str() + colon + 1, "%d", &line);
        if (line > 0)
            fprintf(f, "#line %d \"%s\"\n", line, file.c_str());
    };

    // Generate C code
    FILE *out = fopen(output_file, "w");
    fprintf(out, "// Auto-generated cycle-based state machine from %s\n", verilog_file);
    fprintf(out, "// Generated by gen_statemachine via Yosys RTLIL\n\n");
    fprintf(out, "#include <stdint.h>\n");
    fprintf(out, "#include <stdio.h>\n\n");

    // Input struct (primary inputs, excluding clk/rst)
    fprintf(out, "typedef struct {\n");
    bool has_inputs = false;
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_input) {
            std::string wn = cname(wire->name.str());
            if (wn != "_clk" && wn != "_rst") {
                fprintf(out, "    uint64_t %s;  // %d bits\n", wn.c_str(), wire->width);
                has_inputs = true;
            }
        }
    }
    if (!has_inputs) fprintf(out, "    int _dummy;\n");
    fprintf(out, "} inputs_t;\n\n");

    // State struct
    fprintf(out, "typedef struct {\n");
    for (auto &reg : registers)
        fprintf(out, "    uint64_t %s;  // %d bits\n", reg.name.c_str(), reg.width);
    for (auto &m : memories)
        fprintf(out, "    uint64_t %s[%d];  // %d x %d-bit\n",
                m.second.name.c_str(), m.second.depth, m.second.depth, m.second.width);
    fprintf(out, "} state_t;\n\n");

    // FSM coverage struct
    if (!fsms.empty()) {
        fprintf(out, "#define SM_NUM_FSMS %zu\n", fsms.size());
        fprintf(out, "typedef struct {\n");
        for (auto &fsm : fsms) {
            fprintf(out, "    uint8_t  %s_seen[%d];          // state coverage\n",
                    fsm.name.c_str(), fsm.max_states);
            fprintf(out, "    uint8_t  %s_trans[%d][%d];     // transition coverage\n",
                    fsm.name.c_str(), fsm.max_states, fsm.max_states);
            fprintf(out, "    uint64_t %s_prev;              // previous state value\n",
                    fsm.name.c_str());
            fprintf(out, "    int      %s_valid;             // prev is valid\n",
                    fsm.name.c_str());
        }
        fprintf(out, "    uint64_t cycle_count;\n");
        fprintf(out, "} fsm_coverage_t;\n\n");
        fprintf(out, "static fsm_coverage_t sm_fsm_cov;\n\n");
    }

    // Output struct (observable signals)
    fprintf(out, "typedef struct {\n");
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_output)
            fprintf(out, "    uint64_t %s;  // %d bits\n",
                    cname(wire->name.str()).c_str(), wire->width);
    }
    fprintf(out, "} outputs_t;\n\n");

    // Reset function
    fprintf(out, "void sm_reset(state_t *s) {\n");
    for (auto &reg : registers)
        fprintf(out, "    s->%s = UINT64_C(0x%llx);\n",
                reg.name.c_str(), (unsigned long long)reg.arst_val);
    for (auto &m : memories) {
        auto &mem = m.second;
        for (int i = 0; i < mem.depth; i++) {
            auto it = mem.init.find(i);
            uint64_t val = (it != mem.init.end()) ? it->second : 0;
            if (val != 0)
                fprintf(out, "    s->%s[%d] = UINT64_C(0x%llx);\n",
                        mem.name.c_str(), i, (unsigned long long)val);
        }
    }
    fprintf(out, "}\n\n");

    // Cycle evaluation function
    fprintf(out, "void sm_eval(state_t *s, const inputs_t *in, outputs_t *o) {\n");
    fprintf(out, "    // Input aliases\n");
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_input) {
            std::string wn = cname(wire->name.str());
            if (wn == "_clk" || wn == "_rst")
                fprintf(out, "    uint64_t %s = 0;  // clock/reset handled externally\n", wn.c_str());
            else
                fprintf(out, "    uint64_t %s = in->%s;\n", wn.c_str(), wn.c_str());
        }
    }
    fprintf(out, "\n    // Register aliases (current state)\n");
    for (auto &reg : registers)
        fprintf(out, "    uint64_t %s = s->%s;\n", reg.name.c_str(), reg.name.c_str());
    fprintf(out, "\n");

    // Declare all wires not already covered by registers or inputs
    fprintf(out, "    // Combinational wires\n");
    std::set<std::string> declared;
    for (auto &reg : registers)
        declared.insert(reg.name);
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_input) {
            declared.insert(cname(wire->name.str()));
            continue;
        }
    }
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        std::string wn = cname(wire->name.str());
        if (!declared.count(wn)) {
            fprintf(out, "    uint64_t %s = 0;\n", wn.c_str());
            declared.insert(wn);
        }
    }
    fprintf(out, "\n");

    // Emit combinational logic in topological order
    fprintf(out, "    // Combinational evaluation (topologically sorted)\n");
    for (auto *cell : sorted) {
        auto type = cell->type.str();
        std::string y_name;
        int y_width = 0;
        if (cell->hasPort(ID::Y)) {
            auto &y = cell->getPort(ID::Y);
            if (y.chunks().begin() != y.chunks().end() && (*y.chunks().begin()).wire) {
                y_name = cname((*y.chunks().begin()).wire->name.str());
                y_width = y.size();
            }
        }
        // Emit source location for debugger
        emit_line_directive(out, cell);

        // Handle $memrd separately (uses DATA port, not Y)
        if (type == "$memrd" || type == "$memrd_v2") {
            std::string memid = cell->getParam(ID(MEMID)).decode_string();
            auto &data_port = cell->getPort(ID(DATA));
            std::string data_name;
            if (data_port.chunks().begin() != data_port.chunks().end() &&
                (*data_port.chunks().begin()).wire)
                data_name = cname((*data_port.chunks().begin()).wire->name.str());
            if (!data_name.empty()) {
                auto addr = sig_expr(cell->getPort(ID(ADDR)), sigmap);
                int abits = cell->getParam(ID(ABITS)).as_int();
                uint64_t addr_mask = (1ULL << abits) - 1;
                fprintf(out, "    %s = s->%s[%s & UINT64_C(0x%llx)];\n",
                        data_name.c_str(), cname(memid).c_str(),
                        addr.c_str(), (unsigned long long)addr_mask);
            }
            continue;
        }

        if (y_name.empty()) continue;

        uint64_t mask = y_width >= 64 ? ~0ULL : ((1ULL << y_width) - 1);

        if (type == "$add") {
            fprintf(out, "    %s = (%s + %s) & UINT64_C(0x%llx);\n",
                    y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    (unsigned long long)mask);
        } else if (type == "$sub") {
            fprintf(out, "    %s = (%s - %s) & UINT64_C(0x%llx);\n",
                    y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    (unsigned long long)mask);
        } else if (type == "$and") {
            fprintf(out, "    %s = %s & %s;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str());
        } else if (type == "$or") {
            fprintf(out, "    %s = %s | %s;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str());
        } else if (type == "$xor") {
            fprintf(out, "    %s = %s ^ %s;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str());
        } else if (type == "$not") {
            fprintf(out, "    %s = (~%s) & UINT64_C(0x%llx);\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    (unsigned long long)mask);
        } else if (type == "$shl") {
            fprintf(out, "    %s = (%s << %s) & UINT64_C(0x%llx);\n",
                    y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    (unsigned long long)mask);
        } else if (type == "$shr") {
            fprintf(out, "    %s = %s >> %s;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str());
        } else if (type == "$eq") {
            fprintf(out, "    %s = (%s == %s) ? 1 : 0;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str());
        } else if (type == "$logic_not") {
            fprintf(out, "    %s = (%s == 0) ? 1 : 0;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str());
        } else if (type == "$reduce_or") {
            fprintf(out, "    %s = (%s != 0) ? 1 : 0;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str());
        } else if (type == "$pmux") {
            // Priority mux: A is default, B is concatenated alternatives, S selects
            auto a_expr = sig_expr(cell->getPort(ID::A), sigmap);
            auto &b_sig = cell->getPort(ID::B);
            auto &s_sig = cell->getPort(ID::S);
            int n_cases = s_sig.size();
            int data_width = y_width;

            fprintf(out, "    %s = %s;  // default\n", y_name.c_str(), a_expr.c_str());
            // Each select bit chooses a slice of B
            for (int i = 0; i < n_cases; i++) {
                auto s_bit = sig_expr(s_sig.extract(i, 1), sigmap);
                auto b_slice = sig_expr(b_sig.extract(i * data_width, data_width), sigmap);
                fprintf(out, "    if (%s) %s = %s;\n",
                        s_bit.c_str(), y_name.c_str(), b_slice.c_str());
            }
        } else if (type == "$mux") {
            fprintf(out, "    %s = %s ? %s : %s;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::S), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str());
        } else if (type == "$ne") {
            fprintf(out, "    %s = (%s != %s) ? 1 : 0;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str());
        } else if (type == "$lt") {
            auto a = sig_expr(cell->getPort(ID::A), sigmap);
            auto b = sig_expr(cell->getPort(ID::B), sigmap);
            if (is_signed(cell)) {
                int aw = cell->getPort(ID::A).size();
                int bw = cell->getPort(ID::B).size();
                a = signed_expr(a, aw); b = signed_expr(b, bw);
            }
            fprintf(out, "    %s = (%s < %s) ? 1 : 0;\n", y_name.c_str(), a.c_str(), b.c_str());
        } else if (type == "$le") {
            auto a = sig_expr(cell->getPort(ID::A), sigmap);
            auto b = sig_expr(cell->getPort(ID::B), sigmap);
            if (is_signed(cell)) {
                int aw = cell->getPort(ID::A).size();
                int bw = cell->getPort(ID::B).size();
                a = signed_expr(a, aw); b = signed_expr(b, bw);
            }
            fprintf(out, "    %s = (%s <= %s) ? 1 : 0;\n", y_name.c_str(), a.c_str(), b.c_str());
        } else if (type == "$gt") {
            auto a = sig_expr(cell->getPort(ID::A), sigmap);
            auto b = sig_expr(cell->getPort(ID::B), sigmap);
            if (is_signed(cell)) {
                int aw = cell->getPort(ID::A).size();
                int bw = cell->getPort(ID::B).size();
                a = signed_expr(a, aw); b = signed_expr(b, bw);
            }
            fprintf(out, "    %s = (%s > %s) ? 1 : 0;\n", y_name.c_str(), a.c_str(), b.c_str());
        } else if (type == "$ge") {
            auto a = sig_expr(cell->getPort(ID::A), sigmap);
            auto b = sig_expr(cell->getPort(ID::B), sigmap);
            if (is_signed(cell)) {
                int aw = cell->getPort(ID::A).size();
                int bw = cell->getPort(ID::B).size();
                a = signed_expr(a, aw); b = signed_expr(b, bw);
            }
            fprintf(out, "    %s = (%s >= %s) ? 1 : 0;\n", y_name.c_str(), a.c_str(), b.c_str());
        } else if (type == "$mul") {
            fprintf(out, "    %s = (%s * %s) & UINT64_C(0x%llx);\n",
                    y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    (unsigned long long)mask);
        } else if (type == "$neg") {
            fprintf(out, "    %s = (-%s) & UINT64_C(0x%llx);\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    (unsigned long long)mask);
        } else if (type == "$reduce_and") {
            auto a = sig_expr(cell->getPort(ID::A), sigmap);
            int a_width = cell->getPort(ID::A).size();
            uint64_t a_mask = a_width >= 64 ? ~0ULL : ((1ULL << a_width) - 1);
            fprintf(out, "    %s = ((%s & UINT64_C(0x%llx)) == UINT64_C(0x%llx)) ? 1 : 0;\n",
                    y_name.c_str(), a.c_str(),
                    (unsigned long long)a_mask, (unsigned long long)a_mask);
        } else if (type == "$reduce_xor") {
            fprintf(out, "    { uint64_t _t = %s; _t ^= _t >> 32; _t ^= _t >> 16; "
                    "_t ^= _t >> 8; _t ^= _t >> 4; _t ^= _t >> 2; _t ^= _t >> 1; "
                    "%s = _t & 1; }\n",
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(), y_name.c_str());
        } else if (type == "$reduce_bool") {
            fprintf(out, "    %s = (%s != 0) ? 1 : 0;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str());
        } else if (type == "$xnor") {
            fprintf(out, "    %s = (~(%s ^ %s)) & UINT64_C(0x%llx);\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    (unsigned long long)mask);
        } else {
            fprintf(out, "    // TODO: unhandled cell type %s\n", type.c_str());
        }
    }
    fprintf(out, "\n");

    // Update registers (next state)
    fprintf(out, "    // Register updates (next state)\n");
    for (auto &reg : registers) {
        // Emit source location for register assignment
        if (!reg.src.empty()) {
            auto colon = reg.src.rfind(':');
            if (colon != std::string::npos) {
                std::string file = reg.src.substr(0, colon);
                int line = 0;
                sscanf(reg.src.c_str() + colon + 1, "%d", &line);
                if (line > 0)
                    fprintf(out, "#line %d \"%s\"\n", line, file.c_str());
            }
        }
        if (reg.en_expr.empty())
            fprintf(out, "    s->%s = %s;\n", reg.name.c_str(), reg.d_expr.c_str());
        else
            fprintf(out, "    if (%s) s->%s = %s;\n",
                    reg.en_expr.c_str(), reg.name.c_str(), reg.d_expr.c_str());
    }
    fprintf(out, "\n");

    // FSM coverage tracking
    if (!fsms.empty()) {
        fprintf(out, "    // FSM coverage update\n");
        fprintf(out, "    sm_fsm_cov.cycle_count++;\n");
        for (auto &fsm : fsms) {
            uint64_t mask = fsm.max_states - 1;
            fprintf(out, "    {\n");
            fprintf(out, "        uint64_t _cur = s->%s & UINT64_C(0x%llx);\n",
                    fsm.name.c_str(), (unsigned long long)mask);
            fprintf(out, "        sm_fsm_cov.%s_seen[_cur] = 1;\n", fsm.name.c_str());
            fprintf(out, "        if (sm_fsm_cov.%s_valid)\n", fsm.name.c_str());
            fprintf(out, "            sm_fsm_cov.%s_trans[sm_fsm_cov.%s_prev][_cur] = 1;\n",
                    fsm.name.c_str(), fsm.name.c_str());
            fprintf(out, "        sm_fsm_cov.%s_prev = _cur;\n", fsm.name.c_str());
            fprintf(out, "        sm_fsm_cov.%s_valid = 1;\n", fsm.name.c_str());
            fprintf(out, "    }\n");
        }
        fprintf(out, "\n");
    }

    // Copy outputs — trace through sigmap to find the actual source
    fprintf(out, "    // Outputs\n");
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_output) {
            std::string wn = cname(wire->name.str());
            SigSpec port_sig(wire);
            std::string expr = sig_expr(port_sig, sigmap);
            fprintf(out, "    o->%s = %s;\n", wn.c_str(), expr.c_str());
        }
    }
    fprintf(out, "}\n\n");

    // FSM coverage report function
    if (!fsms.empty()) {
        fprintf(out, "void sm_fsm_coverage_report(FILE *f) {\n");
        fprintf(out, "    fprintf(f, \"=== FSM Coverage Report (%%lu cycles) ===\\n\",\n");
        fprintf(out, "            (unsigned long)sm_fsm_cov.cycle_count);\n");
        for (auto &fsm : fsms) {
            fprintf(out, "    {\n");
            fprintf(out, "        int states_hit = 0, trans_hit = 0;\n");
            fprintf(out, "        for (int i = 0; i < %d; i++)\n", fsm.max_states);
            fprintf(out, "            if (sm_fsm_cov.%s_seen[i]) states_hit++;\n",
                    fsm.name.c_str());
            fprintf(out, "        for (int i = 0; i < %d; i++)\n", fsm.max_states);
            fprintf(out, "            for (int j = 0; j < %d; j++)\n", fsm.max_states);
            fprintf(out, "                if (sm_fsm_cov.%s_trans[i][j]) trans_hit++;\n",
                    fsm.name.c_str());
            fprintf(out, "        fprintf(f, \"  FSM '%s' (%d-bit, %d max states):\\n\");\n",
                    fsm.name.c_str(), fsm.width, fsm.max_states);
            fprintf(out, "        fprintf(f, \"    States visited: %%d / %d\\n\", states_hit);\n",
                    fsm.max_states);
            fprintf(out, "        fprintf(f, \"    Transitions:    %%d\\n\", trans_hit);\n");
            fprintf(out, "        fprintf(f, \"    State detail:\");\n");
            fprintf(out, "        for (int i = 0; i < %d; i++)\n", fsm.max_states);
            fprintf(out, "            if (sm_fsm_cov.%s_seen[i])\n", fsm.name.c_str());
            fprintf(out, "                fprintf(f, \" %%d\", i);\n");
            fprintf(out, "        fprintf(f, \"\\n\");\n");
            fprintf(out, "        fprintf(f, \"    Transitions detail:\\n\");\n");
            fprintf(out, "        for (int i = 0; i < %d; i++)\n", fsm.max_states);
            fprintf(out, "            for (int j = 0; j < %d; j++)\n", fsm.max_states);
            fprintf(out, "                if (sm_fsm_cov.%s_trans[i][j])\n", fsm.name.c_str());
            fprintf(out, "                    fprintf(f, \"      %%d -> %%d\\n\", i, j);\n");
            fprintf(out, "    }\n");
        }
        fprintf(out, "}\n\n");
    }

    // Testbench — auto-generated from output ports
    fprintf(out, "#ifndef SM_NO_MAIN\n");
    fprintf(out, "int main(void) {\n");
    fprintf(out, "    state_t s;\n");
    fprintf(out, "    inputs_t in = {0};\n");
    fprintf(out, "    outputs_t o;\n");
    fprintf(out, "    int cycles = 2000000;\n");
    fprintf(out, "    sm_reset(&s);\n");
    fprintf(out, "    for (int i = 0; i < cycles; i++)\n");
    fprintf(out, "        sm_eval(&s, &in, &o);\n");
    fprintf(out, "    printf(\"Cycles: %%d\\n\", cycles);\n");
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_output) {
            std::string wn = cname(wire->name.str());
            if (wire->width > 32)
                fprintf(out, "    printf(\"%s: %%016llx\\n\", (unsigned long long)o.%s);\n",
                        wn.c_str(), wn.c_str());
            else if (wire->width > 8)
                fprintf(out, "    printf(\"%s: %%08x\\n\", (unsigned)o.%s);\n",
                        wn.c_str(), wn.c_str());
            else
                fprintf(out, "    printf(\"%s: %%02x\\n\", (unsigned)o.%s);\n",
                        wn.c_str(), wn.c_str());
        }
    }
    if (!fsms.empty())
        fprintf(out, "    sm_fsm_coverage_report(stdout);\n");
    fprintf(out, "    return 0;\n");
    fprintf(out, "}\n");
    fprintf(out, "#endif // SM_NO_MAIN\n");

    fclose(out);
    fprintf(stderr, "Generated %s: %zu comb cells, %zu registers\n",
            output_file, sorted.size(), registers.size());

    // --- Generate NVC-mapped version ---
    // Wraps the standalone sm_eval with signal bridge code.
    // Compiled as .so, loaded by cycle_sim plugin.
    std::string mapped_file = std::string(output_file);
    auto dot = mapped_file.rfind('.');
    if (dot != std::string::npos)
        mapped_file = mapped_file.substr(0, dot) + "_nvc.c";
    else
        mapped_file += "_nvc.c";

    FILE *mout = fopen(mapped_file.c_str(), "w");
    fprintf(mout, "// NVC-mapped state machine — bridges sm_eval with NVC signal storage\n");
    fprintf(mout, "// Generated by gen_statemachine via Yosys RTLIL\n\n");

    // Include the standalone version (minus main, added via #define guard)
    fprintf(mout, "#define SM_NO_MAIN 1\n");
    fprintf(mout, "#include \"%s\"\n\n", output_file);

    // Signal bridge
    fprintf(mout, "static state_t sm_state;\n");
    fprintf(mout, "static inputs_t sm_inputs;\n");
    fprintf(mout, "static outputs_t sm_outputs;\n\n");

    // NVC signal storage pointers
    fprintf(mout, "static uint8_t *sm_reg_ptrs[%zu];\n", registers.size());
    fprintf(mout, "static int sm_reg_widths[%zu];\n\n", registers.size());

    // Signal name table for plugin binding
    // Strip leading _ (Yosys \ prefix) and uppercase (VHDL convention)
    // to match NVC's internal signal names
    fprintf(mout, "const char *sm_reg_names[] = {");
    for (auto &reg : registers) {
        std::string name = reg.name;
        if (!name.empty() && name[0] == '_') name = name.substr(1);
        for (auto &c : name) c = toupper(c);
        fprintf(mout, "\"%s\", ", name.c_str());
    }
    fprintf(mout, "};\n");
    fprintf(mout, "int sm_n_regs = %zu;\n\n", registers.size());

    // Read NVC byte-per-bit signals into state struct
    size_t ri = 0;
    fprintf(mout, "static void sm_read_nvc(void) {\n");
    for (auto &reg : registers) {
        fprintf(mout, "    { uint64_t v=0; for(int b=0; b<sm_reg_widths[%zu]; b++) "
                "v|=(uint64_t)(sm_reg_ptrs[%zu][b]&1)<<b; sm_state.%s=v; }\n",
                ri, ri, reg.name.c_str());
        ri++;
    }
    fprintf(mout, "}\n\n");

    // Write state struct back to NVC byte-per-bit signals
    ri = 0;
    fprintf(mout, "static void sm_write_nvc(void) {\n");
    for (auto &reg : registers) {
        fprintf(mout, "    { uint64_t v=sm_state.%s; for(int b=0; b<sm_reg_widths[%zu]; b++) "
                "sm_reg_ptrs[%zu][b]=(v>>b)&1; }\n",
                reg.name.c_str(), ri, ri);
        ri++;
    }
    fprintf(mout, "}\n\n");

    // Plugin API
    fprintf(mout, "void sm_init_mapped(uint8_t **ptrs, int *widths, int n) {\n");
    fprintf(mout, "    for(int i=0; i<%zu && i<n; i++) { sm_reg_ptrs[i]=ptrs[i]; sm_reg_widths[i]=widths[i]; }\n",
            registers.size());
    fprintf(mout, "    sm_reset(&sm_state);\n");
    fprintf(mout, "    sm_write_nvc();\n");
    fprintf(mout, "}\n\n");

    fprintf(mout, "void sm_eval_mapped(void) {\n");
    fprintf(mout, "    sm_read_nvc();\n");
    fprintf(mout, "    sm_eval(&sm_state, &sm_inputs, &sm_outputs);\n");
    fprintf(mout, "    sm_write_nvc();\n");
    fprintf(mout, "}\n\n");

    fprintf(mout, "void sm_reset_mapped(void) {\n");
    fprintf(mout, "    sm_reset(&sm_state);\n");
    fprintf(mout, "    sm_write_nvc();\n");
    fprintf(mout, "}\n");

    fclose(mout);
    fprintf(stderr, "Generated %s (NVC-mapped version)\n", mapped_file.c_str());

    Yosys::yosys_shutdown();
    return 0;
}
