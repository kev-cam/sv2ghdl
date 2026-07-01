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

// Read redirect: maps each sigmap-representative SigBit to the net's actual
// DRIVEN storage bit (a cell output Y/DATA, a register Q, or a module input).
// yosys's SigMap frequently elects an UNDRIVEN port/alias wire as a connect
// group's representative (ports are preferred reps), so a read of that rep would
// emit a dead 0 wire while the real value lives under the driver's name. Built
// once after sigmap (g_build_redirect); identity for alias-free designs, so the
// narrow toy output stays byte-identical.
static std::map<RTLIL::SigBit, RTLIL::SigBit> *g_redirect = nullptr;

// Sanitize RTLIL names to valid C identifiers
static std::string cname(const std::string &s) {
    std::string r;
    for (char c : s) {
        if (c == ']') continue;          // drop closing bracket (paired with '[')
        else if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                 || (c >= '0' && c <= '9') || c == '_')
            r += c;
        else
            r += '_';                    // \ $ . / : [ - space etc. -> _ (valid C id)
    }
    // Prefix if starts with digit
    if (!r.empty() && (r[0] >= '0' && r[0] <= '9'))
        r = "w_" + r;
    return r;
}

// Wide-signal support: signals up to 64 bits use uint64_t (byte-identical to the
// pre-wide codegen); 65..128 bits use unsigned __int128 so big datapath chunks
// (e.g. dec's 68-bit i0_brp) compute correctly instead of truncating to 64.
static const char *ctype(int w) { return w > 64 ? "unsigned __int128" : "uint64_t"; }

// Emit a C literal for a value up to 128 bits (hi/lo split for >64); for <=64
// emits exactly the old `UINT64_C(0x..)` spelling so narrow output is unchanged.
static std::string u128_lit(unsigned __int128 v) {
    std::ostringstream o;
    uint64_t lo = (uint64_t)v, hi = (uint64_t)(v >> 64);
    if (hi == 0)
        o << "UINT64_C(0x" << std::hex << lo << ")";
    else
        o << "((((unsigned __int128)UINT64_C(0x" << std::hex << hi << "))<<64)|UINT64_C(0x"
          << std::hex << lo << "))";
    return o.str();
}

// Emit a C mask literal for a width 1..128, computed with __int128 at codegen so
// the UB `1<<w` (w>=64) never runs; <=64 matches the old `UINT64_C(0x..)` spelling.
static std::string mask_lit(int w) {
    if (w >= 128) return "(~(unsigned __int128)0)";
    if (w == 64)  return "UINT64_C(0xffffffffffffffff)";
    if (w < 64) {
        std::ostringstream o;
        o << "UINT64_C(0x" << std::hex << ((UINT64_C(1) << w) - 1) << ")";
        return o.str();
    }
    return u128_lit(((unsigned __int128)1 << w) - 1);   // 65..127
}

// ---- Scalable wide-signal (>64b) support: little-endian uint32_t limbs ----
// Signals up to 64 bits stay scalar uint64_t (byte-identical to the pre-wide
// codegen, and fast — the common narrow datapath). Wider signals become
// `uint32_t name[nlimbs]` arrays evaluated by the small wide-int runtime below.
// 32-bit limbs keep every $mul partial product inside a uint64_t (no __int128),
// so this scales to ANY width — e.g. dec's 152-bit trigger_pkt_any.
static inline bool is_wide(int w) { return w > 64; }
static inline int  nlimbs(int w)  { return (w + 31) / 32; }

// Emitted into the generated .c ONLY when some signal is wide (so all-narrow
// designs stay byte-identical). Little-endian: limb i holds bits [32i, 32i+32).
// Callers size every operand to the op's limb count (via emit_materialize), so
// these helpers take a single length n; results are masked to width by the caller.
static const char *WIDE_RT =
"// --- wide-int runtime (little-endian uint32_t limbs) ---\n"
"static inline void wcopy(uint32_t*d,const uint32_t*a,int n){for(int i=0;i<n;i++)d[i]=a[i];}\n"
"static inline void wand(uint32_t*d,const uint32_t*a,const uint32_t*b,int n){for(int i=0;i<n;i++)d[i]=a[i]&b[i];}\n"
"static inline void wor_(uint32_t*d,const uint32_t*a,const uint32_t*b,int n){for(int i=0;i<n;i++)d[i]=a[i]|b[i];}\n"
"static inline void wxor(uint32_t*d,const uint32_t*a,const uint32_t*b,int n){for(int i=0;i<n;i++)d[i]=a[i]^b[i];}\n"
"static inline void wnot(uint32_t*d,const uint32_t*a,int n){for(int i=0;i<n;i++)d[i]=~a[i];}\n"
"static inline void wadd(uint32_t*d,const uint32_t*a,const uint32_t*b,int n){uint64_t c=0;for(int i=0;i<n;i++){c+=(uint64_t)a[i]+b[i];d[i]=(uint32_t)c;c>>=32;}}\n"
"static inline void wsub(uint32_t*d,const uint32_t*a,const uint32_t*b,int n){uint64_t c=1;for(int i=0;i<n;i++){c+=(uint64_t)a[i]+(uint32_t)~b[i];d[i]=(uint32_t)c;c>>=32;}}\n"
"static inline void wneg(uint32_t*d,const uint32_t*a,int n){uint64_t c=1;for(int i=0;i<n;i++){c+=(uint64_t)(uint32_t)~a[i];d[i]=(uint32_t)c;c>>=32;}}\n"
"static inline void wmul(uint32_t*d,const uint32_t*a,const uint32_t*b,int n){uint32_t t[128];for(int i=0;i<n;i++)t[i]=0;for(int i=0;i<n;i++){uint64_t c=0;for(int j=0;i+j<n;j++){uint64_t p=(uint64_t)a[i]*b[j]+t[i+j]+c;t[i+j]=(uint32_t)p;c=p>>32;}}for(int i=0;i<n;i++)d[i]=t[i];}\n"
"static inline void wshl(uint32_t*d,const uint32_t*a,int s,int n){int w=s>>5,b=s&31;for(int i=n-1;i>=0;i--){uint32_t v=0;int j=i-w;if(j>=0){v=a[j]<<b;if(b&&j-1>=0)v|=a[j-1]>>(32-b);}d[i]=v;}}\n"
"static inline void wshr(uint32_t*d,const uint32_t*a,int s,int n){int w=s>>5,b=s&31;for(int i=0;i<n;i++){uint32_t v=0;int j=i+w;if(j<n){v=a[j]>>b;if(b&&j+1<n)v|=a[j+1]<<(32-b);}d[i]=v;}}\n"
"static inline int weq(const uint32_t*a,const uint32_t*b,int n){for(int i=0;i<n;i++)if(a[i]!=b[i])return 0;return 1;}\n"
"static inline int wult(const uint32_t*a,const uint32_t*b,int n){for(int i=n-1;i>=0;i--)if(a[i]!=b[i])return a[i]<b[i];return 0;}\n"
"static inline int wslt(const uint32_t*a,const uint32_t*b,int n){uint32_t sa=a[n-1]>>31,sb=b[n-1]>>31;if(sa!=sb)return sa;return wult(a,b,n);}\n"
"static inline int wred_or(const uint32_t*a,int n){for(int i=0;i<n;i++)if(a[i])return 1;return 0;}\n"
"static inline int wred_xor(const uint32_t*a,int n){uint32_t x=0;for(int i=0;i<n;i++)x^=a[i];x^=x>>16;x^=x>>8;x^=x>>4;x^=x>>2;x^=x>>1;return x&1;}\n"
"static inline int wred_and(const uint32_t*a,int width,int n){for(int i=0;i<n;i++){uint32_t m=(i==n-1&&(width&31))?((1u<<(width&31))-1):0xffffffffu;if((a[i]&m)!=m)return 0;}return 1;}\n"
"static inline uint64_t wslice64(const uint32_t*s,int off,int w,int n){int l=off>>5,b=off&31;uint64_t lo=s[l];if(l+1<n)lo|=(uint64_t)s[l+1]<<32;uint64_t v=lo>>b;if(b&&w+b>64&&l+2<n)v|=(uint64_t)s[l+2]<<(64-b);return w>=64?v:(v&((UINT64_C(1)<<w)-1));}\n"
"static inline void wplace(uint32_t*d,int off,const uint32_t*s,int w){for(int i=0;i<w;i++){int p=off+i;uint32_t b=(s[i>>5]>>(i&31))&1;d[p>>5]=(d[p>>5]&~(1u<<(p&31)))|(b<<(p&31));}}\n"
"static inline void wplaceb(uint32_t*d,int off,uint32_t b){d[off>>5]=(d[off>>5]&~(1u<<(off&31)))|((b&1)<<(off&31));}\n"
"\n";

// Get a C expression for a SigSpec (wire reference or constant)
static std::string sig_expr(const SigSpec &sig, const SigMap &sigmap) {
    RTLIL::SigSpec mapped = sigmap(sig);
    // Redirect each representative bit to its actual driven storage bit, so a
    // read of an undriven connect-group representative resolves to the driver
    // (cell/register/input) instead of a dead 0 wire. Identity (no remap) for
    // alias-free designs -> narrow output unchanged.
    if (g_redirect != nullptr && !g_redirect->empty()) {
        RTLIL::SigSpec rm;
        for (RTLIL::SigBit bit : mapped) {   // by value — operator* yields a temporary
            auto it = g_redirect->find(bit);
            rm.append(it != g_redirect->end() ? it->second : bit);
        }
        mapped = rm;
    }
    if (mapped.is_fully_const()) {
        auto val = mapped.as_const();
        unsigned __int128 v = 0;
        for (int i = val.size()-1; i >= 0; i--)
            v = (v << 1) | (val[i] == RTLIL::S1 ? 1 : 0);
        return u128_lit(v);
    }

    // Single wire reference. NB: hold chunks() in a NAMED local. SigSpec::chunks()
    // returns a temporary vector; binding `auto &chunk = *chunks().begin()` makes a
    // reference into a temporary destroyed at end-of-statement -> stack-use-after-
    // scope, which only crashes once -O2 reuses the stack slot (ASan-confirmed).
    auto chunks = mapped.chunks();
    if (chunks.size() == 1) {
        RTLIL::SigChunk chunk = *chunks.begin();   // COPY by value: *iterator returns a ref into the
        if (chunk.wire) {                          // temporary iterator's own member -> would dangle if bound by ref
            std::string wn = cname(chunk.wire->name.str());
            // Wide SOURCE wire (uint32_t[] limbs) read as a <=64-bit scalar by a
            // narrow consumer: extract the slice from the limb array. (Wide
            // consumers never reach sig_expr — they go through emit_materialize.)
            if (is_wide(chunk.wire->width)) {
                std::ostringstream oss;
                oss << "wslice64(" << wn << "," << chunk.offset << ","
                    << chunk.width << "," << nlimbs(chunk.wire->width) << ")";
                return oss.str();
            }
            if (chunk.offset == 0 && chunk.width == chunk.wire->width)
                return wn;
            else if (chunk.width == 1)
                return "(((" + wn + ") >> " + std::to_string(chunk.offset) + ") & 1)";
            else {
                std::ostringstream oss;
                oss << "(((" << wn << ") >> " << chunk.offset << ") & " << mask_lit(chunk.width) << ")";
                return oss.str();
            }
        }
    }

    // Multi-chunk: build by concatenation
    std::string expr = "0";
    int pos = 0;
    for (auto &chunk : chunks) {
        std::string part;
        if (chunk.wire) {
            std::string wn = cname(chunk.wire->name.str());
            if (is_wide(chunk.wire->width)) {
                std::ostringstream oss;
                oss << "wslice64(" << wn << "," << chunk.offset << ","
                    << chunk.width << "," << nlimbs(chunk.wire->width) << ")";
                part = oss.str();
            }
            else if (chunk.offset == 0 && chunk.width == chunk.wire->width)
                part = wn;
            else {
                std::ostringstream oss;
                oss << "((" << wn << " >> " << chunk.offset << ") & " << mask_lit(chunk.width) << ")";
                part = oss.str();
            }
        } else {
            unsigned __int128 v = 0;
            for (int i = chunk.data.size()-1; i >= 0; i--)
                v = (v << 1) | (chunk.data[i] == RTLIL::S1 ? 1 : 0);
            part = u128_lit(v);
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
    if (width >= 128) return "(__int128)" + expr;
    if (width > 64) {   // 65..127: sign-extend within an __int128
        std::ostringstream oss;
        oss << "((__int128)((unsigned __int128)(" << expr << ") << " << (128 - width)
            << ") >> " << (128 - width) << ")";
        return oss.str();
    }
    if (width >= 64) return "(int64_t)" + expr;
    std::ostringstream oss;
    oss << "((int64_t)((" << expr << ") << " << (64 - width) << ") >> " << (64 - width) << ")";
    return oss.str();
}

// Read a SigSpec as a <=64-bit scalar C expression. For a >64-bit signal (shift
// amounts, mux/pmux selects — always small) take the low 64 bits; sig_expr reads
// wide sources via wslice64, so this is correct for any source representation.
static std::string scalar_of(const SigSpec &sig, const SigMap &sigmap) {
    if (sig.size() <= 64) return sig_expr(sig, sigmap);
    return sig_expr(sig.extract(0, 64), sigmap);
}

// Mask a wide limb array's top limb down to `width` bits (no-op if width is a
// multiple of 32 — the top limb is already full).
static void emit_wmask(FILE *o, const std::string &y, int width, int ny) {
    if (width & 31)
        fprintf(o, "      %s[%d] &= 0x%xu;\n", y.c_str(), ny - 1,
                (unsigned)((1u << (width & 31)) - 1));
}

// Fill dst[0..ny) limbs with the value of `sig`, zero- or sign-extended to ny
// limbs. `dst` is a uint32_t[ny] C lvalue (e.g. "_wa", "s->reg", "o->port").
// Per-bit placement handles every operand shape uniformly — wire/slice/concat/
// const, narrow scalar source OR wide limb source, any limb-straddling offset.
static void emit_materialize(FILE *o, const std::string &dst, int ny,
                             const SigSpec &sig, const SigMap &sigmap,
                             bool signext, int sext_w) {
    auto mapped = sigmap(sig);
    fprintf(o, "      for(int _wi=0;_wi<%d;_wi++) %s[_wi]=0;\n", ny, dst.c_str());
    std::vector<uint32_t> cacc(ny, 0);
    bool any_const = false;
    int pos = 0;
    for (auto &chunk : mapped.chunks()) {
        int w = chunk.width;
        if (chunk.wire) {
            std::string wn = cname(chunk.wire->name.str());
            int off = chunk.offset;
            // Loop var is _wk (NOT _wb — that name is used for an operand temp
            // array in emit_wide_cell; a _wb counter would shadow it and the
            // dst[_wk] subscript would hit an int).
            if (is_wide(chunk.wire->width))
                fprintf(o, "      for(int _wk=0;_wk<%d;_wk++){ uint32_t _wx="
                        "(%s[(%d+_wk)>>5]>>((%d+_wk)&31))&1;"
                        " if(_wx) %s[(%d+_wk)>>5]|=1u<<((%d+_wk)&31); }\n",
                        w, wn.c_str(), off, off, dst.c_str(), pos, pos);
            else
                fprintf(o, "      for(int _wk=0;_wk<%d;_wk++){ uint32_t _wx="
                        "(uint32_t)((%s>>(%d+_wk))&1);"
                        " if(_wx) %s[(%d+_wk)>>5]|=1u<<((%d+_wk)&31); }\n",
                        w, wn.c_str(), off, dst.c_str(), pos, pos);
        } else {
            any_const = true;
            for (int i = 0; i < w; i++) {
                bool bit = (i < (int)chunk.data.size() && chunk.data[i] == RTLIL::S1);
                if (bit) { int p = pos + i; if ((p >> 5) < ny) cacc[p >> 5] |= 1u << (p & 31); }
            }
        }
        pos += w;
    }
    if (any_const)
        for (int i = 0; i < ny; i++)
            if (cacc[i]) fprintf(o, "      %s[%d]|=0x%xu;\n", dst.c_str(), i, cacc[i]);
    if (signext && sext_w >= 1) {
        int sb = sext_w - 1, tl = sb >> 5;
        uint32_t hm = (sb & 31) == 31 ? 0 : (0xffffffffu << ((sb & 31) + 1));
        fprintf(o, "      if(%s[%d]&(1u<<%d)){", dst.c_str(), tl, sb & 31);
        if (hm) fprintf(o, " %s[%d]|=0x%xu;", dst.c_str(), tl, hm);
        for (int i = tl + 1; i < ny; i++) fprintf(o, " %s[%d]=0xffffffffu;", dst.c_str(), i);
        fprintf(o, " }\n");
    }
}

// Emit a wide cell — any cell whose operands OR whose TARGET WIRE exceed 64
// bits. Mirrors the scalar op chain in limb arithmetic. The result is written
// into the target at its bit offset `yoff`: when the target wire is wide
// (`twide`) via wplace/wplaceb (a read-modify-write, so a wire driven in several
// sub-slices by different cells composes correctly); otherwise to a <=64b scalar.
// `yw` is the cell's Y width (slice width), not the whole wire.
static void emit_wide_cell(FILE *o, RTLIL::Cell *cell, SigMap &sigmap,
                           const std::string &y, int yw, int yoff, bool twide,
                           int aw, int bw) {
    auto type = cell->type.str();
    int ny = nlimbs(yw > 0 ? yw : 1);
    auto matA = [&](const char *d, int nl, bool sx, int sw) {
        emit_materialize(o, d, nl, cell->getPort(ID::A), sigmap, sx, sw); };
    auto matB = [&](const char *d, int nl, bool sx, int sw) {
        emit_materialize(o, d, nl, cell->getPort(ID::B), sigmap, sx, sw); };
    // Store the yw-bit result held in _wy (ng limbs) into the target.
    auto put_val = [&](int ng) {
        if (twide) fprintf(o, "      wplace(%s,%d,_wy,%d);\n", y.c_str(), yoff, yw);
        else       fprintf(o, "      %s = wslice64(_wy,0,%d,%d);\n", y.c_str(), yw, ng); };
    // Store a 1-bit result expression into the target.
    auto put_bit = [&](const std::string &e) {
        if (twide) fprintf(o, "      wplaceb(%s,%d,%s);\n", y.c_str(), yoff, e.c_str());
        else       fprintf(o, "      %s = %s;\n", y.c_str(), e.c_str()); };

    fprintf(o, "    {\n");
    if (type == "$add" || type == "$sub" || type == "$mul" ||
        type == "$and" || type == "$or" || type == "$xor") {
        int ng = nlimbs(std::max(yw, std::max(aw, bw)));
        fprintf(o, "      uint32_t _wa[%d],_wb[%d],_wy[%d];\n", ng, ng, ng);
        matA("_wa", ng, false, 0); matB("_wb", ng, false, 0);
        const char *fn = type == "$add" ? "wadd" : type == "$sub" ? "wsub" :
                         type == "$mul" ? "wmul" : type == "$and" ? "wand" :
                         type == "$or" ? "wor_" : "wxor";
        fprintf(o, "      %s(_wy,_wa,_wb,%d);\n", fn, ng);
        put_val(ng);
    } else if (type == "$xnor") {
        int ng = nlimbs(std::max(yw, std::max(aw, bw)));
        fprintf(o, "      uint32_t _wa[%d],_wb[%d],_wy[%d];\n", ng, ng, ng);
        matA("_wa", ng, false, 0); matB("_wb", ng, false, 0);
        fprintf(o, "      wxor(_wy,_wa,_wb,%d); wnot(_wy,_wy,%d);\n", ng, ng);
        put_val(ng);
    } else if (type == "$not" || type == "$neg") {
        int ng = nlimbs(std::max(yw, aw));
        fprintf(o, "      uint32_t _wa[%d],_wy[%d];\n", ng, ng);
        matA("_wa", ng, false, 0);
        fprintf(o, "      %s(_wy,_wa,%d);\n", type == "$not" ? "wnot" : "wneg", ng);
        put_val(ng);
    } else if (type == "$shl" || type == "$shr") {
        int ng = nlimbs(std::max(yw, aw));
        fprintf(o, "      uint32_t _wa[%d],_wy[%d]; int _sh=(int)(%s);\n",
                ng, ng, scalar_of(cell->getPort(ID::B), sigmap).c_str());
        matA("_wa", ng, false, 0);
        fprintf(o, "      %s(_wy,_wa,_sh,%d);\n", type == "$shl" ? "wshl" : "wshr", ng);
        put_val(ng);
    } else if (type == "$mux") {
        fprintf(o, "      uint32_t _wa[%d],_wb[%d],_wy[%d];\n", ny, ny, ny);
        matA("_wa", ny, false, 0); matB("_wb", ny, false, 0);
        fprintf(o, "      if (%s) wcopy(_wy,_wb,%d); else wcopy(_wy,_wa,%d);\n",
                scalar_of(cell->getPort(ID::S), sigmap).c_str(), ny, ny);
        put_val(ny);
    } else if (type == "$pmux") {
        int n_cases = cell->getPort(ID::S).size();
        fprintf(o, "      uint32_t _wy[%d];\n", ny);
        emit_materialize(o, "_wy", ny, cell->getPort(ID::A), sigmap, false, 0);
        for (int i = 0; i < n_cases; i++) {
            fprintf(o, "      if (%s) {\n",
                    scalar_of(cell->getPort(ID::S).extract(i, 1), sigmap).c_str());
            emit_materialize(o, "_wy", ny, cell->getPort(ID::B).extract(i * yw, yw), sigmap, false, 0);
            fprintf(o, "      }\n");
        }
        put_val(ny);
    } else if (type == "$eq" || type == "$ne") {
        int nc = nlimbs(std::max(aw, bw));
        fprintf(o, "      uint32_t _wa[%d],_wb[%d];\n", nc, nc);
        matA("_wa", nc, false, 0); matB("_wb", nc, false, 0);
        char e[64]; snprintf(e, sizeof e, "weq(_wa,_wb,%d)?%d:%d", nc,
                             type == "$eq" ? 1 : 0, type == "$eq" ? 0 : 1);
        put_bit(e);
    } else if (type == "$lt" || type == "$le" || type == "$gt" || type == "$ge") {
        bool sg = is_signed(cell);
        int nc = nlimbs(std::max(aw, bw));
        fprintf(o, "      uint32_t _wa[%d],_wb[%d];\n", nc, nc);
        matA("_wa", nc, sg, aw); matB("_wb", nc, sg, bw);
        const char *cmp = sg ? "wslt" : "wult";
        char e[64];
        if (type == "$lt")      snprintf(e, sizeof e, "%s(_wa,_wb,%d)?1:0", cmp, nc);
        else if (type == "$gt") snprintf(e, sizeof e, "%s(_wb,_wa,%d)?1:0", cmp, nc);
        else if (type == "$le") snprintf(e, sizeof e, "%s(_wb,_wa,%d)?0:1", cmp, nc);
        else                    snprintf(e, sizeof e, "%s(_wa,_wb,%d)?0:1", cmp, nc);
        put_bit(e);
    } else if (type == "$reduce_or" || type == "$reduce_bool" || type == "$logic_not") {
        int na = nlimbs(aw);
        fprintf(o, "      uint32_t _wa[%d];\n", na); matA("_wa", na, false, 0);
        char e[48]; snprintf(e, sizeof e, "wred_or(_wa,%d)?%d:%d", na,
                             type == "$logic_not" ? 0 : 1, type == "$logic_not" ? 1 : 0);
        put_bit(e);
    } else if (type == "$reduce_and") {
        int na = nlimbs(aw);
        fprintf(o, "      uint32_t _wa[%d];\n", na); matA("_wa", na, false, 0);
        char e[48]; snprintf(e, sizeof e, "wred_and(_wa,%d,%d)?1:0", aw, na);
        put_bit(e);
    } else if (type == "$reduce_xor") {
        int na = nlimbs(aw);
        fprintf(o, "      uint32_t _wa[%d];\n", na); matA("_wa", na, false, 0);
        char e[48]; snprintf(e, sizeof e, "wred_xor(_wa,%d)?1:0", na);
        put_bit(e);
    } else {
        fprintf(o, "      // TODO: unhandled WIDE cell type %s (yw=%d aw=%d bw=%d)\n",
                type.c_str(), yw, aw, bw);
    }
    fprintf(o, "    }\n");
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
    // Accept multiple input files: any arg ending in .v/.sv/.vh/.svh is a
    // source; the first non-source arg is the top module; the next is the
    // output .c. This lets a multi-file DUT (e.g. a_plus_b + its fifos) be
    // compiled straight from the original sources, with no concatenation.
    std::vector<std::string> inputs;
    std::vector<std::string> params;   // "name=value" generic overrides
    const char *top_name = NULL;
    const char *output_file = NULL;
    for (int i = 1; i < argc; i++) {
        std::string a(argv[i]);
        auto ends = [&](const char *e) {
            std::string s(e);
            return a.size() >= s.size()
                && a.compare(a.size() - s.size(), s.size(), s) == 0;
        };
        if (ends(".sv") || ends(".v") || ends(".vh") || ends(".svh"))
            inputs.push_back(a);
        else if (a.find('=') != std::string::npos)
            params.push_back(a);
        else if (!top_name)    top_name = argv[i];
        else if (!output_file) output_file = argv[i];
    }
    if (inputs.empty()) inputs.push_back("/tmp/rtl_design.v");
    if (!top_name)      top_name = "rtl_top";
    fprintf(stderr, "  inputs: %zu  top: %s\n", inputs.size(), top_name);

    // Default output: ~/.cache/nvc/accel/accel-mod_<top>.c
    std::string default_output;
    if (!output_file) {
        default_output = accel_cache_path(top_name, ".c");
        output_file = default_output.c_str();
    }

    Yosys::yosys_setup();
    if (getenv("GSM_LOG")) {            // surface yosys diagnostics for debugging
        Yosys::log_streams.push_back(&std::cerr);
        Yosys::log_error_stderr = true;
    }

    // Read each source (-sv for .sv); read_verilog accumulates modules.
    for (const std::string &f : inputs) {
        std::string sv_flag =
            (f.size() >= 3 && f.substr(f.size() - 3) == ".sv") ? " -sv" : "";
        Yosys::run_pass(std::string("read_verilog") + sv_flag + " " + f);
    }
    // Apply generic/parameter overrides on the top module so hierarchy
    // elaborates with the SAME values the nvc instance used (e.g. width/depth).
    // nvc recovers ALL module generics from the elaborated tree, which includes
    // derived localparams (counter_width, max_ptr, pointer_width, ...). Those are
    // not settable parameters — yosys chparam aborts on them — and they recompute
    // from the real parameters anyway, so skip any name the module doesn't declare
    // as an available parameter.
    // Snapshot the settable parameter names BEFORE any chparam — chparam mutates
    // (and may replace) the module, so re-reading avail_parameters mid-loop is
    // stale. Copy the names, not the pointer.
    std::set<std::string> settable;
    if (RTLIL::Module *topmod0 =
            yosys_get_design()->module(RTLIL::escape_id(top_name)))
        for (auto id : topmod0->avail_parameters)
            settable.insert(id.str());
    for (const std::string &p : params) {
        size_t eq = p.find('=');
        std::string k = p.substr(0, eq), v = p.substr(eq + 1);
        if (!settable.empty() && !settable.count(RTLIL::escape_id(k))) {
            fprintf(stderr, "  skip %s=%s (not a settable parameter of %s)\n",
                    k.c_str(), v.c_str(), top_name);
            continue;
        }
        fprintf(stderr, "  chparam %s = %s on %s\n", k.c_str(), v.c_str(), top_name);
        Yosys::run_pass("chparam -set " + k + " " + v + " " + top_name);
    }
    Yosys::run_pass(std::string("hierarchy -top ") + top_name);
    Yosys::run_pass("proc");
    Yosys::run_pass("flatten");
    Yosys::run_pass("opt");
    // Lower clock-enable and SYNCHRONOUS reset into the FF's D logic ($sdff/
    // $dffe -> plain $dff + an explicit mux). Async resets ($adff) are left for
    // sm_reset. Without this the codegen drops sync resets (q_next = d only).
    Yosys::run_pass("dffunmap");
    Yosys::run_pass("opt_clean");   // tidy WITHOUT opt_dff re-absorbing the reset into $dff SRST

    auto *design = Yosys::yosys_get_design();
    auto *mod = design->top_module();
    SigMap sigmap(mod);

    // Build the read-redirect: for every DRIVEN net (cell output Y/DATA, register
    // Q, module input), map its sigmap representative bit -> the raw driven bit.
    // A read then resolves through sig_expr to the driver's own C name even when
    // sigmap elected an undriven port/alias wire as the group representative
    // (the root cause of dec's dead-wire miscompute). One driver per net, so no
    // conflicts; identity for alias-free designs.
    std::map<RTLIL::SigBit, RTLIL::SigBit> redirect;
    {
        auto markspec = [&](const RTLIL::SigSpec &s) {
            RTLIL::SigSpec m = sigmap(s);
            for (int i = 0; i < GetSize(s); i++)
                if (s[i].wire) redirect[m[i]] = s[i];
        };
        for (auto &cp : mod->cells_) {
            RTLIL::Cell *cell = cp.second;
            for (auto pid : {ID::Y, ID(DATA), ID::Q})
                if (cell->hasPort(pid)) markspec(cell->getPort(pid));
        }
        for (auto &wp : mod->wires_)
            if (wp.second->port_input) markspec(RTLIL::SigSpec(wp.second));
    }
    g_redirect = &redirect;

    // Collect all wires, identify registers
    struct RegInfo {
        std::string name;
        std::string d_expr;       // narrow (<=64b) D as a scalar C expr
        RTLIL::SigSpec d_sig;      // wide (>64b) D, committed via emit_materialize
        std::string en_expr;  // empty = always enabled
        std::string src;      // source location "file:line.col"
        int width;
        unsigned __int128 arst_val;
        std::string arst_expr; // async-reset ($adff) assert condition; "" = none
        std::string clk_name; // CLK net cname (redirect-folded); "_clk" = main clk
        int clk_group = 0;    // 0 = main clk; 1+i = extra_clocks[i]
    };
    struct MemInfo {
        std::string name;
        int width;          // bits per word
        int depth;          // number of words
        int abits;          // address bits
        std::map<int, unsigned __int128> init;  // addr -> value
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
            unsigned __int128 data = 0;
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
            reg.width = q.size();
            reg.d_sig = d;
            // Wide D is committed via emit_materialize (sig_expr can't return a
            // limb array); only render the scalar string for narrow regs.
            reg.d_expr = is_wide(reg.width) ? "" : sig_expr(d, sigmap);
            reg.src = cell->get_src_attribute();

            // Get async reset value + assert condition if present. $adff resets
            // ASYNCHRONOUSLY (level-sensitive on ARST), so beyond the initial value
            // (sm_reset) we must force the reg to arst_val whenever ARST is asserted
            // during operation — not just at a clock edge. Capture the ARST net (as
            // a C condition, polarity-normalized to "true == reset asserted").
            reg.arst_val = 0;
            if (type == "$adff" || type == "$adffe") {
                auto arst_val = cell->getParam(ID(ARST_VALUE));
                unsigned __int128 rv = 0;
                for (int i = arst_val.size()-1; i >= 0; i--)
                    rv = (rv << 1) | (arst_val[i] == RTLIL::S1 ? 1 : 0);
                reg.arst_val = rv;
                if (cell->hasPort(ID::ARST)) {
                    std::string a = sig_expr(cell->getPort(ID::ARST), sigmap);
                    bool pol = !cell->hasParam(ID(ARST_POLARITY))
                             || cell->getParam(ID(ARST_POLARITY)).as_bool();
                    reg.arst_expr = pol ? a : ("(!(" + a + "))");
                }
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

            // Clock net (fold through redirect exactly like data reads, so an
            // internal gated clock that `connect`s to \clk -> cname "_clk" -> main
            // group). The bridge advances each flop on ITS clock's posedge.
            reg.clk_name = "_clk";
            if (cell->hasPort(ID::CLK)) {
                RTLIL::SigBit cb = sigmap(cell->getPort(ID::CLK))[0];
                auto rit = redirect.find(cb);
                if (rit != redirect.end()) cb = rit->second;
                if (cb.wire) reg.clk_name = cname(cb.wire->name.str());
            }

            registers.push_back(reg);
        } else {
            comb_cells.push_back(cell);
        }
    }

    // --- Multi-clock grouping ---
    // Group registers by their clock net. Group 0 = the main clk (cname "_clk",
    // which the bridge drives the posedge from). Each DISTINCT other clock (e.g.
    // free_clk / active_clk, which are module INPUTS = clk & enable) becomes an
    // extra group whose flops the bridge advances on THAT clock's own posedge.
    std::vector<std::string> extra_clocks;
    {
        std::set<std::string> input_cnames;
        for (auto &w : mod->wires_)
            if (w.second->port_input) input_cnames.insert(cname(w.second->name.str()));
        for (auto &reg : registers) {
            if (reg.clk_name == "_clk") continue;
            if (std::find(extra_clocks.begin(), extra_clocks.end(), reg.clk_name)
                == extra_clocks.end()) {
                // The bridge can only edge-detect a clock that is a boundary INPUT.
                // An internal generated clock that did not fold to \clk can't be
                // tracked -> decline (stay interpreted) rather than miscompute.
                if (!input_cnames.count(reg.clk_name)) {
                    fprintf(stderr, "gen_statemachine: extra clock %s is not a "
                            "module input — declining\n", reg.clk_name.c_str());
                    exit(1);
                }
                extra_clocks.push_back(reg.clk_name);
            }
        }
        for (auto &reg : registers)
            reg.clk_group = (reg.clk_name == "_clk") ? 0
                : 1 + (int)(std::find(extra_clocks.begin(), extra_clocks.end(),
                                      reg.clk_name) - extra_clocks.begin());
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

    // Build dependency graph for topological sort. Map output wire -> the
    // cell(s) producing it. A wire driven in several sub-slices (common for a
    // wide register's D input, each slice a separate cell) has MULTIPLE drivers;
    // all must be ordered before a reader, else a reader sees a partially-written
    // wire. (Single-driver wires keep a 1-element list -> identical topo order.)
    // Key by the SIGMAP-CANONICAL output net, NOT the raw Y wire. Reads go
    // through sigmap (sig_expr), so the driver and its readers must agree on the
    // canonical net; for a wire in a `connect`/alias group the raw Y wire differs
    // from the canonical, which would emit `rawY = ...` while readers read the
    // canonical -> the value never flows (silent dead wire). (Alias-free designs:
    // sigmap is identity, so unchanged.)
    std::map<RTLIL::Wire*, std::vector<RTLIL::Cell*>> wire_driver;
    for (auto *cell : comb_cells) {
        // Check Y port (most cells) and DATA port ($memrd). Key by the RAW driven
        // wire (the name the cell writes); readers reach it through the redirect.
        for (auto port_id : {ID::Y, ID(DATA)}) {
            if (cell->hasPort(port_id)) {
                for (auto &chunk : cell->getPort(port_id).chunks())
                    if (chunk.wire) wire_driver[chunk.wire].push_back(cell);
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
        // Visit dependencies (input wires). Resolve each input bit to its DRIVEN
        // wire through sigmap+redirect (an aliased net's connect is folded here),
        // then look up the raw-keyed wire_driver — so a dependency through a
        // connect/alias is not missed (which left the topo order undefined and
        // emitted a consumer before its producer).
        for (auto &conn : cell->connections()) {
            if (conn.first == ID::Y) continue;  // skip output
            RTLIL::SigSpec m = sigmap(conn.second);
            for (auto &bit : m) {
                auto rit = redirect.find(bit);
                RTLIL::Wire *w = (rit != redirect.end() ? rit->second : bit).wire;
                if (w == nullptr) continue;
                auto it = wire_driver.find(w);
                if (it != wire_driver.end())
                    for (auto *dc : it->second) topo_visit(dc);
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
    fprintf(out, "// Auto-generated cycle-based state machine from %s%s\n",
            inputs[0].c_str(), inputs.size() > 1 ? " (+more)" : "");
    fprintf(out, "// Generated by gen_statemachine via Yosys RTLIL\n\n");
    fprintf(out, "#include <stdint.h>\n");
    fprintf(out, "#include <stdio.h>\n\n");

    // Emit the wide-int runtime only when some signal exceeds 64 bits, so
    // all-narrow designs are byte-identical to the pre-wide codegen.
    bool any_wide = false;
    for (auto &w : mod->wires_)
        if (is_wide(w.second->width)) { any_wide = true; break; }
    if (any_wide) fprintf(out, "%s", WIDE_RT);

    // Input struct (primary inputs, excluding clk/rst)
    fprintf(out, "typedef struct {\n");
    bool has_inputs = false;
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_input) {
            std::string wn = cname(wire->name.str());
            if (wn != "_clk" && wn != "_rst") {
                if (is_wide(wire->width))
                    fprintf(out, "    uint32_t %s[%d];  // %d bits\n",
                            wn.c_str(), nlimbs(wire->width), wire->width);
                else
                    fprintf(out, "    %s %s;  // %d bits\n", ctype(wire->width), wn.c_str(), wire->width);
                has_inputs = true;
            }
        }
    }
    if (!has_inputs) fprintf(out, "    int _dummy;\n");
    fprintf(out, "} inputs_t;\n\n");

    // Wide-word memories (>64b/word) are out of scope: the $memwr/$memrd data
    // path below stays uint64_t, so silently truncating a wide word would be
    // wrong. Decline the whole module (stays interpreted in nvc) instead.
    for (auto &m : memories)
        if (m.second.width > 64) {
            fprintf(stderr, "gen_statemachine: memory %s has %d-bit words (>64) — declining\n",
                    m.second.name.c_str(), m.second.width);
            exit(1);   // install checks the exit code and leaves the chunk in nvc
        }

    // State struct
    fprintf(out, "typedef struct {\n");
    for (auto &reg : registers) {
        if (is_wide(reg.width))
            fprintf(out, "    uint32_t %s[%d];  // %d bits\n",
                    reg.name.c_str(), nlimbs(reg.width), reg.width);
        else
            fprintf(out, "    %s %s;  // %d bits\n", ctype(reg.width), reg.name.c_str(), reg.width);
    }
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
        if (wire->port_output) {
            if (is_wide(wire->width))
                fprintf(out, "    uint32_t %s[%d];  // %d bits\n",
                        cname(wire->name.str()).c_str(), nlimbs(wire->width), wire->width);
            else
                fprintf(out, "    %s %s;  // %d bits\n",
                        ctype(wire->width), cname(wire->name.str()).c_str(), wire->width);
        }
    }
    fprintf(out, "} outputs_t;\n\n");

    // Reset function
    fprintf(out, "void sm_reset(state_t *s) {\n");
    for (auto &reg : registers) {
        if (is_wide(reg.width)) {
            // arst_val is an __int128 (reset bits >128 are 0 — unsupported, rare).
            int ny = nlimbs(reg.width);
            for (int l = 0; l < ny; l++) {
                uint32_t lw = (l < 4) ? (uint32_t)(reg.arst_val >> (32 * l)) : 0;
                fprintf(out, "    s->%s[%d] = 0x%xu;\n", reg.name.c_str(), l, lw);
            }
        } else
            fprintf(out, "    s->%s = %s;\n",
                    reg.name.c_str(), u128_lit(reg.arst_val).c_str());
    }
    for (auto &m : memories) {
        auto &mem = m.second;
        for (int i = 0; i < mem.depth; i++) {
            auto it = mem.init.find(i);
            unsigned __int128 val = (it != mem.init.end()) ? it->second : 0;
            if (val != 0)
                fprintf(out, "    s->%s[%d] = %s;\n",
                        mem.name.c_str(), i, u128_lit(val).c_str());
        }
    }
    fprintf(out, "}\n\n");

    // Cycle evaluation, SPLIT into sm_comb (combinational outputs from the
    // CURRENT register state + inputs, no commit) and sm_clock (advance the
    // registers/memory to the next state). The --accel bridge re-runs sm_comb on
    // every boundary-input-change delta (intra-cycle combinational settling) and
    // sm_clock once per clock posedge. sm_eval is kept as a back-compat wrapper.
    // Shared preamble (aliases + comb-wire decls + topological comb eval) lambda:
    auto emit_comb = [&](const char *sp) {
    fprintf(out, "    // Input aliases\n");
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_input) {
            std::string wn = cname(wire->name.str());
            if (wn == "_clk" || wn == "_rst")
                fprintf(out, "    uint64_t %s = 0;  // clock/reset handled externally\n", wn.c_str());
            else if (is_wide(wire->width))
                fprintf(out, "    uint32_t %s[%d]; wcopy(%s,in->%s,%d);\n",
                        wn.c_str(), nlimbs(wire->width), wn.c_str(), wn.c_str(), nlimbs(wire->width));
            else
                fprintf(out, "    %s %s = in->%s;\n", ctype(wire->width), wn.c_str(), wn.c_str());
        }
    }
    fprintf(out, "\n    // Register aliases (current state)\n");
    for (auto &reg : registers) {
        if (is_wide(reg.width))
            fprintf(out, "    uint32_t %s[%d]; wcopy(%s,%s->%s,%d);\n",
                    reg.name.c_str(), nlimbs(reg.width), reg.name.c_str(), sp, reg.name.c_str(), nlimbs(reg.width));
        else
            fprintf(out, "    %s %s = %s->%s;\n", ctype(reg.width), reg.name.c_str(), sp, reg.name.c_str());
    }
    fprintf(out, "\n");

    // Async reset ($adff/$adffe): level-sensitive. Whenever ARST is asserted, force
    // the register to its reset value NOW — both the local snapshot (so this eval's
    // combinational outputs reflect the reset) and the persistent state (so it holds
    // through to the next clock edge, matching async-reset hardware). Without this,
    // the reset value was only applied once at sm_reset() and a mid-cycle reset that
    // followed a clocked load stuck at the stale value. The clocked commit in
    // emit_seq is gated off while ARST is asserted so it can't clobber the reset.
    {
        bool hdr = false;
        for (auto &reg : registers) {
            if (reg.arst_expr.empty()) continue;
            if (!hdr) { fprintf(out, "    // Async reset overrides\n"); hdr = true; }
            if (is_wide(reg.width)) {
                int ny = nlimbs(reg.width);
                fprintf(out, "    if (%s) {", reg.arst_expr.c_str());
                for (int l = 0; l < ny; l++) {
                    uint32_t lw = (l < 4) ? (uint32_t)(reg.arst_val >> (32*l)) : 0;
                    fprintf(out, " %s[%d]=0x%xu; %s->%s[%d]=0x%xu;",
                            reg.name.c_str(), l, lw, sp, reg.name.c_str(), l, lw);
                }
                fprintf(out, " }\n");
            } else {
                std::string rv = u128_lit(reg.arst_val);
                fprintf(out, "    if (%s) { %s = %s; %s->%s = %s; }\n",
                        reg.arst_expr.c_str(), reg.name.c_str(), rv.c_str(),
                        sp, reg.name.c_str(), rv.c_str());
            }
        }
        if (hdr) fprintf(out, "\n");
    }

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
            if (is_wide(wire->width))
                fprintf(out, "    uint32_t %s[%d] = {0};\n", wn.c_str(), nlimbs(wire->width));
            else
                fprintf(out, "    %s %s = 0;\n", ctype(wire->width), wn.c_str());
            declared.insert(wn);
        }
    }
    fprintf(out, "\n");

    // Emit combinational logic in topological order
    fprintf(out, "    // Combinational evaluation (topologically sorted)\n");
    for (auto *cell : sorted) {
        auto type = cell->type.str();
        std::string y_name;
        int y_width = 0, y_off = 0, ywire_w = 0;
        if (cell->hasPort(ID::Y)) {
            // The cell writes its RAW Y wire (its driven storage). Readers resolve
            // to this name through the g_redirect map (sig_expr), so aliased
            // (connect-group) nets read the driver's value rather than a dead wire.
            auto &y = cell->getPort(ID::Y);
            if (y.chunks().begin() != y.chunks().end() && (*y.chunks().begin()).wire) {
                RTLIL::SigChunk yc = *y.chunks().begin();
                y_name  = cname(yc.wire->name.str());
                y_width = y.size();
                y_off   = yc.offset;          // cell may drive a SLICE of the wire
                ywire_w = yc.wire->width;
            }
        }
        // Emit source location for debugger
        emit_line_directive(out, cell);

        // Handle $memrd separately (uses DATA port, not Y)
        if (type == "$memrd" || type == "$memrd_v2") {
            std::string memid = cell->getParam(ID(MEMID)).decode_string();
            auto &data_port = cell->getPort(ID(DATA));   // raw driven storage
            std::string data_name;
            if (data_port.chunks().begin() != data_port.chunks().end() &&
                (*data_port.chunks().begin()).wire)
                data_name = cname((*data_port.chunks().begin()).wire->name.str());
            if (!data_name.empty()) {
                auto addr = sig_expr(cell->getPort(ID(ADDR)), sigmap);
                int abits = cell->getParam(ID(ABITS)).as_int();
                fprintf(out, "    %s = s->%s[%s & %s];\n",
                        data_name.c_str(), cname(memid).c_str(),
                        addr.c_str(), mask_lit(abits).c_str());
            }
            continue;
        }

        if (y_name.empty()) continue;

        // Wide cell: any operand, the Y slice, OR the TARGET WIRE exceeds 64
        // bits. (A narrow slice of a wide wire — a partial drive — must still go
        // wide, else we'd emit `wide_array = scalar`.) The scalar chain below is
        // left byte-identical and only runs for fully-narrow cells.
        int aw_ = cell->hasPort(ID::A) ? cell->getPort(ID::A).size() : 0;
        int bw_ = cell->hasPort(ID::B) ? cell->getPort(ID::B).size() : 0;
        if (is_wide(ywire_w) || is_wide(y_width) || is_wide(aw_) || is_wide(bw_)) {
            emit_wide_cell(out, cell, sigmap, y_name, y_width, y_off,
                           is_wide(ywire_w), aw_, bw_);
            continue;
        }

        std::string masks = mask_lit(y_width);   // width-correct C mask string

        // Multi-wire Y: a cell whose output is a CONCATENATION of distinct wires
        // (e.g. a $mux/$pmux from the procedural sv_and/sv_or helpers drives
        // {wireA, wireB}). The op below writes ONE name; emitting only the first
        // chunk's wire leaves the others dead-0 (the root cause of dec's
        // miscompute). Run the op into a temp, then scatter it to every Y chunk.
        std::vector<RTLIL::SigChunk> y_chunks;
        bool y_multi = false;
        if (cell->hasPort(ID::Y)) {
            auto yc = cell->getPort(ID::Y).chunks();
            int nwire = 0; for (auto &c : yc) if (c.wire) nwire++;
            if (nwire > 1) { y_chunks.assign(yc.begin(), yc.end()); y_multi = true; }
        }
        if (y_multi) { fprintf(out, "    { uint64_t _yspl = 0;\n"); y_name = "_yspl"; }

        if (type == "$add") {
            fprintf(out, "    %s = (%s + %s) & %s;\n",
                    y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    masks.c_str());
        } else if (type == "$sub") {
            fprintf(out, "    %s = (%s - %s) & %s;\n",
                    y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    masks.c_str());
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
            fprintf(out, "    %s = (~%s) & %s;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    masks.c_str());
        } else if (type == "$shl") {
            fprintf(out, "    %s = (%s << %s) & %s;\n",
                    y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    masks.c_str());
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
            // a >64b product needs a wide multiply (two uint64_t operands would
            // lose the high half before masking); cast one operand to the Y type.
            if (y_width > 64)
                fprintf(out, "    %s = ((unsigned __int128)(%s) * (%s)) & %s;\n",
                        y_name.c_str(),
                        sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                        sig_expr(cell->getPort(ID::B), sigmap).c_str(), masks.c_str());
            else
                fprintf(out, "    %s = (%s * %s) & %s;\n",
                        y_name.c_str(),
                        sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                        sig_expr(cell->getPort(ID::B), sigmap).c_str(), masks.c_str());
        } else if (type == "$neg") {
            fprintf(out, "    %s = (-%s) & %s;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(), masks.c_str());
        } else if (type == "$reduce_and") {
            auto a = sig_expr(cell->getPort(ID::A), sigmap);
            int a_width = cell->getPort(ID::A).size();
            std::string am = mask_lit(a_width);
            fprintf(out, "    %s = ((%s & %s) == %s) ? 1 : 0;\n",
                    y_name.c_str(), a.c_str(), am.c_str(), am.c_str());
        } else if (type == "$reduce_xor") {
            int a_width = cell->getPort(ID::A).size();
            if (a_width > 64)
                fprintf(out, "    { unsigned __int128 _t = %s; _t ^= _t >> 64; _t ^= _t >> 32; "
                        "_t ^= _t >> 16; _t ^= _t >> 8; _t ^= _t >> 4; _t ^= _t >> 2; _t ^= _t >> 1; "
                        "%s = (uint64_t)(_t & 1); }\n",
                        sig_expr(cell->getPort(ID::A), sigmap).c_str(), y_name.c_str());
            else
                fprintf(out, "    { uint64_t _t = %s; _t ^= _t >> 32; _t ^= _t >> 16; "
                        "_t ^= _t >> 8; _t ^= _t >> 4; _t ^= _t >> 2; _t ^= _t >> 1; "
                        "%s = _t & 1; }\n",
                        sig_expr(cell->getPort(ID::A), sigmap).c_str(), y_name.c_str());
        } else if (type == "$reduce_bool") {
            fprintf(out, "    %s = (%s != 0) ? 1 : 0;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str());
        } else if (type == "$xnor") {
            fprintf(out, "    %s = (~(%s ^ %s)) & %s;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(), masks.c_str());
        } else {
            fprintf(out, "    // TODO: unhandled cell type %s\n", type.c_str());
        }
        if (y_multi) {
            int pos = 0;
            for (auto &ch : y_chunks) {
                if (ch.wire) {
                    std::string wn = cname(ch.wire->name.str());
                    int w = ch.width, off = ch.offset, ww = ch.wire->width;
                    if (is_wide(ww))   // chunk targets a limb-array wire
                        fprintf(out, "      for(int _sb=0;_sb<%d;_sb++)"
                                " wplaceb(%s,%d+_sb,(uint32_t)((_yspl>>(%d+_sb))&1));\n",
                                w, wn.c_str(), off, pos);
                    else if (off == 0 && w == ww)
                        fprintf(out, "      %s = (_yspl >> %d) & %s;\n",
                                wn.c_str(), pos, mask_lit(w).c_str());
                    else {
                        std::string m = mask_lit(w);
                        fprintf(out, "      %s = (%s & ~(%s << %d)) |"
                                " (((_yspl >> %d) & %s) << %d);\n",
                                wn.c_str(), wn.c_str(), m.c_str(), off, pos, m.c_str(), off);
                    }
                }
                pos += ch.width;
            }
            fprintf(out, "    }\n");
        }
    }
    fprintf(out, "\n");
    };  // end emit_comb lambda

    // Register commits + memory writes + FSM coverage (the sm_clock tail).
    // sp = write-pointer ("s" single-clock; "dst" masked). masked=false emits the
    // ORIGINAL unguarded text (single-clock byte-identical). masked=true guards
    // each commit by its clock group's posedge_mask bit.
    auto emit_seq = [&](const char *sp, bool masked) {
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
        std::string dst = std::string(sp) + "->" + reg.name;
        // Build the commit condition: [group-bit] [&&] [enable].
        std::string cond;
        if (masked) {
            char g[40]; snprintf(g, sizeof g, "(posedge_mask & (1u<<%d))", reg.clk_group);
            cond = g;
            if (!reg.en_expr.empty()) cond += " && (" + reg.en_expr + ")";
        } else cond = reg.en_expr;   // empty or the enable, exactly as before
        // Async-reset regs: the level-sensitive override in emit_comb already forced
        // arst_val whenever ARST is asserted; gate the clocked load off while ARST is
        // asserted so a coincident clock edge can't overwrite the reset value.
        if (!reg.arst_expr.empty()) {
            std::string g = "!(" + reg.arst_expr + ")";
            cond = cond.empty() ? g : (g + " && " + cond);
        }
        if (is_wide(reg.width)) {
            int ny = nlimbs(reg.width);
            if (!cond.empty()) fprintf(out, "    if (%s) {\n", cond.c_str());
            emit_materialize(out, dst, ny, reg.d_sig, sigmap, false, 0);
            emit_wmask(out, dst, reg.width, ny);
            if (!cond.empty()) fprintf(out, "    }\n");
        } else if (cond.empty())
            fprintf(out, "    %s = %s;\n", dst.c_str(), reg.d_expr.c_str());
        else
            fprintf(out, "    if (%s) %s = %s;\n",
                    cond.c_str(), dst.c_str(), reg.d_expr.c_str());
    }

    // Memory WRITE ports ($memwr). These were previously dropped entirely (only
    // $meminit/$memrd were handled), so any design with a writable memory/FIFO got
    // correct pointers but never-written data -> garbage reads. Emit writes here,
    // AFTER the combinational $memrd reads above, so a same-cycle read sees the OLD
    // word (FIFO read-before-write / NBA semantics). ADDR/DATA/EN use the current-
    // state register aliases, so placement after the pointer updates is fine.
    {
        bool hdr = false;
        for (auto &c : mod->cells_) {
            auto *cell = c.second;
            auto wtype = cell->type.str();
            if (wtype != "$memwr" && wtype != "$memwr_v2") continue;
            if (!hdr) { fprintf(out, "    // Memory write ports\n"); hdr = true; }
            std::string mn = cname(cell->getParam(ID(MEMID)).decode_string());
            int abits = cell->getParam(ID(ABITS)).as_int();
            uint64_t amask = (abits >= 64) ? ~0ULL : ((1ULL << abits) - 1);
            std::string addr = sig_expr(cell->getPort(ID(ADDR)), sigmap);
            std::string data = sig_expr(cell->getPort(ID(DATA)), sigmap);
            std::string en   = sig_expr(cell->getPort(ID(EN)), sigmap);
            // Masked write: bits where EN=1 take DATA, EN=0 keep old word. Handles
            // uniform full-word enables (FIFOs) and partial/byte enables alike.
            // Memory writes are owned by the main clk group (bit 0) when masked.
            std::string menc = masked ? std::string("(posedge_mask & 1u) && ") + en : en;
            fprintf(out, "    if (%s) { uint64_t _wa = (%s) & UINT64_C(0x%llx); "
                         "%s->%s[_wa] = (%s->%s[_wa] & ~(uint64_t)(%s)) | ((%s) & (%s)); }\n",
                    menc.c_str(), addr.c_str(), (unsigned long long)amask,
                    sp, mn.c_str(), sp, mn.c_str(), en.c_str(), data.c_str(), en.c_str());
        }
    }
    fprintf(out, "\n");

    // FSM coverage tracking (owned by the main clk group when masked)
    if (!fsms.empty()) {
        if (masked) fprintf(out, "    if (posedge_mask & 1u) {\n");
        fprintf(out, "    // FSM coverage update\n");
        fprintf(out, "    sm_fsm_cov.cycle_count++;\n");
        for (auto &fsm : fsms) {
            uint64_t mask = fsm.max_states - 1;
            fprintf(out, "    {\n");
            fprintf(out, "        uint64_t _cur = %s->%s & UINT64_C(0x%llx);\n",
                    sp, fsm.name.c_str(), (unsigned long long)mask);
            fprintf(out, "        sm_fsm_cov.%s_seen[_cur] = 1;\n", fsm.name.c_str());
            fprintf(out, "        if (sm_fsm_cov.%s_valid)\n", fsm.name.c_str());
            fprintf(out, "            sm_fsm_cov.%s_trans[sm_fsm_cov.%s_prev][_cur] = 1;\n",
                    fsm.name.c_str(), fsm.name.c_str());
            fprintf(out, "        sm_fsm_cov.%s_prev = _cur;\n", fsm.name.c_str());
            fprintf(out, "        sm_fsm_cov.%s_valid = 1;\n", fsm.name.c_str());
            fprintf(out, "    }\n");
        }
        if (masked) fprintf(out, "    }\n");
        fprintf(out, "\n");
    }
    };  // end emit_seq lambda

    // Output copies (the sm_comb tail) — trace through sigmap to find the source.
    auto emit_outputs = [&]() {
    fprintf(out, "    // Outputs\n");
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_output) {
            std::string wn = cname(wire->name.str());
            SigSpec port_sig(wire);
            if (is_wide(wire->width)) {
                std::string dst = "o->" + wn;
                int ny = nlimbs(wire->width);
                emit_materialize(out, dst, ny, port_sig, sigmap, false, 0);
                emit_wmask(out, dst, wire->width, ny);
            } else {
                std::string expr = sig_expr(port_sig, sigmap);
                fprintf(out, "    o->%s = %s;\n", wn.c_str(), expr.c_str());
            }
        }
    }
    };  // end emit_outputs lambda

    // sm_comb: combinational OUTPUTS from the CURRENT register state + inputs,
    // with no side effects (no register/memory commit). The bridge re-runs this
    // on every boundary-input-change delta to settle combinational outputs.
    fprintf(out, "void sm_comb(state_t *s, const inputs_t *in, outputs_t *o) {\n");
    emit_comb("s");
    emit_outputs();
    fprintf(out, "}\n\n");

    // sm_clock: advance the registers / memory to the next state (no outputs).
    // Single-clock designs: byte-identical to before (one unconditional group).
    // Multi-clock: sm_clock_masked advances only the groups whose clock posedged
    // (posedge_mask), reading the pre-edge snapshot `src` (so coincident edges and
    // the cross-delta clock race both sample one frozen pre-edge state).
    if (extra_clocks.empty()) {
        fprintf(out, "void sm_clock(state_t *s, const inputs_t *in) {\n");
        emit_comb("s");
        emit_seq("s", false);
        fprintf(out, "}\n\n");
    } else {
        // Each clock group advances reading the LIVE state at its own delta. A
        // derived gated clock (free_clk = clk & en) posedges a delta AFTER clk in
        // nvc's delta model, so its flops correctly see the post-clk-advance state
        // (matching the interpreted reference) — NOT a frozen pre-edge snapshot.
        // The top-of-emit_comb alias snapshot still gives correct NBA within a
        // single call (any groups co-firing in one mask read one snapshot).
        fprintf(out, "void sm_clock_masked(state_t *s, const inputs_t *in, "
                     "unsigned posedge_mask) {\n");
        emit_comb("s");
        emit_seq("s", true);
        fprintf(out, "}\n\n");
        fprintf(out, "void sm_clock(state_t *s, const inputs_t *in) {\n");
        fprintf(out, "    sm_clock_masked(s, in, ~0u);\n");
        fprintf(out, "}\n\n");
    }
    // Cross-file table: the bridge text-scrapes these to discover the extra clock
    // INPUT field base-names (matching pins[].name / `in._<name>`) and the count,
    // for per-clock edge detection. Always emitted so the symbols resolve.
    fprintf(out, "const char *sm_extra_clocks[] = {");
    for (auto &c : extra_clocks) {
        std::string nm = (!c.empty() && c[0] == '_') ? c.substr(1) : c;
        fprintf(out, "\"%s\", ", nm.c_str());
    }
    fprintf(out, "0};\n");
    fprintf(out, "#define SM_NUM_EXTRA_CLOCKS %zu\n\n", extra_clocks.size());

    // sm_eval: back-compat wrapper — combinational outputs from the current state,
    // then commit (identical to the old single-function semantics).
    fprintf(out, "void sm_eval(state_t *s, const inputs_t *in, outputs_t *o) {\n");
    fprintf(out, "    sm_comb(s, in, o);\n");
    fprintf(out, "    sm_clock(s, in);\n");
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
            if (is_wide(wire->width))
                fprintf(out, "    printf(\"%s: %%08x..\\n\", (unsigned)o.%s[0]);\n",
                        wn.c_str(), wn.c_str());
            else if (wire->width > 32)
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
        if (is_wide(reg.width))
            fprintf(mout, "    { for(int _l=0;_l<%d;_l++) sm_state.%s[_l]=0;"
                    " for(int b=0; b<sm_reg_widths[%zu]; b++)"
                    " sm_state.%s[b>>5]|=(uint32_t)(sm_reg_ptrs[%zu][b]&1)<<(b&31); }\n",
                    nlimbs(reg.width), reg.name.c_str(), ri, reg.name.c_str(), ri);
        else
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
        if (is_wide(reg.width))
            fprintf(mout, "    { for(int b=0; b<sm_reg_widths[%zu]; b++)"
                    " sm_reg_ptrs[%zu][b]=(sm_state.%s[b>>5]>>(b&31))&1; }\n",
                    ri, ri, reg.name.c_str());
        else
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
