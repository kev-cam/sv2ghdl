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

int main(int argc, char **argv)
{
    const char *verilog_file = argc > 1 ? argv[1] : "/tmp/rtl_design.v";
    const char *top_name = argc > 2 ? argv[2] : "rtl_top";
    const char *output_file = argc > 3 ? argv[3] : "/tmp/rtl_statemachine.c";

    Yosys::yosys_setup();

    std::string cmd = std::string("read_verilog ") + verilog_file;
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
            reg.name = cname((*q.chunks().begin()).wire->name.str());
            reg.d_expr = sig_expr(d, sigmap);
            reg.width = q.size();

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
        if (reg.en_expr.empty())
            fprintf(out, "    s->%s = %s;\n", reg.name.c_str(), reg.d_expr.c_str());
        else
            fprintf(out, "    if (%s) s->%s = %s;\n",
                    reg.en_expr.c_str(), reg.name.c_str(), reg.d_expr.c_str());
    }
    fprintf(out, "\n");

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

    // Testbench — auto-generated from output ports
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
    fprintf(out, "    return 0;\n");
    fprintf(out, "}\n");

    fclose(out);
    fprintf(stderr, "Generated %s: %zu comb cells, %zu registers\n",
            output_file, sorted.size(), registers.size());

    Yosys::yosys_shutdown();
    return 0;
}
