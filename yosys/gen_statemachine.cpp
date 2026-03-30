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
        int width;
        uint64_t arst_val;
    };
    std::vector<RegInfo> registers;
    std::vector<RTLIL::Cell*> comb_cells;

    for (auto &c : mod->cells_) {
        auto *cell = c.second;
        auto type = cell->type.str();

        if (type == "$scopeinfo") continue;

        if (type == "$adff") {
            RegInfo reg;
            auto &q = cell->getPort(ID::Q);
            auto &d = cell->getPort(ID::D);
            reg.name = cname((*q.chunks().begin()).wire->name.str());
            reg.d_expr = sig_expr(d, sigmap);
            reg.width = q.size();

            // Get async reset value
            auto arst_val = cell->getParam(ID(ARST_VALUE));
            uint64_t rv = 0;
            for (int i = arst_val.bits().size()-1; i >= 0; i--)
                rv = (rv << 1) | (arst_val[i] == RTLIL::S1 ? 1 : 0);
            reg.arst_val = rv;

            registers.push_back(reg);
        } else {
            comb_cells.push_back(cell);
        }
    }

    // Build dependency graph for topological sort
    // Map output wire -> cell that produces it
    std::map<RTLIL::Wire*, RTLIL::Cell*> wire_driver;
    for (auto *cell : comb_cells) {
        if (cell->hasPort(ID::Y)) {
            auto &y = cell->getPort(ID::Y);
            for (auto &chunk : y.chunks())
                if (chunk.wire) wire_driver[chunk.wire] = cell;
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

    // State struct
    fprintf(out, "typedef struct {\n");
    for (auto &reg : registers)
        fprintf(out, "    uint64_t %s;  // %d bits\n", reg.name.c_str(), reg.width);
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
    fprintf(out, "}\n\n");

    // Cycle evaluation function
    fprintf(out, "void sm_eval(state_t *s, outputs_t *o) {\n");
    fprintf(out, "    // Register aliases (current state)\n");
    for (auto &reg : registers)
        fprintf(out, "    uint64_t %s = s->%s;\n", reg.name.c_str(), reg.name.c_str());
    fprintf(out, "\n");

    // Declare combinational wires
    fprintf(out, "    // Combinational wires\n");
    std::set<std::string> declared;
    for (auto &reg : registers)
        declared.insert(reg.name);
    for (auto *cell : sorted) {
        if (cell->hasPort(ID::Y)) {
            auto &y = cell->getPort(ID::Y);
            for (auto &chunk : y.chunks()) {
                if (chunk.wire) {
                    std::string wn = cname(chunk.wire->name.str());
                    if (!declared.count(wn)) {
                        fprintf(out, "    uint64_t %s;\n", wn.c_str());
                        declared.insert(wn);
                    }
                }
            }
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
        } else {
            fprintf(out, "    // TODO: unhandled cell type %s\n", type.c_str());
        }
    }
    fprintf(out, "\n");

    // Update registers (next state)
    fprintf(out, "    // Register updates (next state)\n");
    for (auto &reg : registers)
        fprintf(out, "    s->%s = %s;\n", reg.name.c_str(), reg.d_expr.c_str());
    fprintf(out, "\n");

    // Copy outputs
    fprintf(out, "    // Outputs\n");
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_output) {
            std::string wn = cname(wire->name.str());
            if (declared.count(wn))
                fprintf(out, "    o->%s = %s;\n", wn.c_str(), wn.c_str());
        }
    }
    fprintf(out, "}\n\n");

    // Testbench
    fprintf(out, "int main(void) {\n");
    fprintf(out, "    state_t s;\n");
    fprintf(out, "    outputs_t o;\n");
    fprintf(out, "    sm_reset(&s);\n");
    fprintf(out, "    for (int i = 0; i < 2000000; i++)\n");
    fprintf(out, "        sm_eval(&s, &o);\n");
    fprintf(out, "    printf(\"Counter: %%u\\n\", (unsigned)o._u_cnt_count);\n");
    fprintf(out, "    printf(\"LFSR:    %%08x\\n\", (unsigned)o._u_lfsr_q);\n");
    fprintf(out, "    printf(\"ALU:     %%02x\\n\", (unsigned)o._alu_result);\n");
    fprintf(out, "    return 0;\n");
    fprintf(out, "}\n");

    fclose(out);
    fprintf(stderr, "Generated %s: %zu comb cells, %zu registers\n",
            output_file, sorted.size(), registers.size());

    Yosys::yosys_shutdown();
    return 0;
}
