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
// FSM per-state specialization (GSM_FSM_SPEC). When g_spec_value >= 0 the
// register-alias for g_spec_regname is emitted as that literal constant, so the
// state's muxes/selects const-fold in the compiler — the "vtable" per-state
// specialized eval. sm_comb/sm_clock become a switch on the real state field
// dispatching to N folded bodies.
static std::string g_spec_regname;
static long        g_spec_value   = -1;
static int         g_spec_nstates = 0;
// Mealy-output classification, captured as the C emitter computes it so the
// CXXRTL adapter emits a byte-identical sm_comb_outputs[] table (the bridge
// scrapes it to choose the deposit region -- ACTIVE vs staged).
static std::vector<std::string> g_comb_out_names;

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

// ---------------------------------------------------------------------------
// VALUE-PLANE ENGINE (GSM_CXXRTL=1)
//
// nvc's 3D-logic carries value / driven / uncertain planes that are INDEPENDENT
// by design. yosys's CXXRTL backend emits fast 2-state C++ over exactly one
// densely packed plane of bits -- which IS the l3d value plane. So under
// GSM_CXXRTL we ALSO run `write_cxxrtl` on the same RTLIL and emit a thin
// ADAPTER that implements nvc's model-side contract (state_t / inputs_t /
// outputs_t + sm_reset/sm_comb/sm_clock/sm_clock_out) on top of the generated
// CXXRTL object.  CXXRTL's own output is never rewritten -- it is #included.
//
// cxx_mangle replicates yosys's cxxrtl_backend.cc mangle_name() exactly:
//   leading '\' -> "p_", leading '$' -> "i_", alnum verbatim, '_' -> "__",
//   anything else -> "_<hexlo><hexhi>_" (lower-case hex, high nibble first).
static std::string cxx_mangle(const std::string &name) {
    std::string mangled;
    bool first = true;
    for (char c : name) {
        if (first) {
            first = false;
            if (c == '\\')      mangled += "p_";
            else if (c == '$')  mangled += "i_";
            else                return std::string();   // unexpected: caller declines
        } else if (isalnum((unsigned char)c)) {
            mangled += c;
        } else if (c == '_') {
            mangled += "__";
        } else {
            char l = c & 0xf, h = (c >> 4) & 0xf;
            mangled += '_';
            mangled += (h < 10 ? '0' + h : 'a' + h - 10);
            mangled += (l < 10 ? '0' + l : 'a' + l - 10);
            mangled += '_';
        }
    }
    return mangled;
}

// A member CXXRTL declared in the generated header: kind is "value" (unbuffered
// -- read/write directly) or "wire" (buffered -- .curr / .next).  Parsed out of
// the generated .h rather than assumed, because at -O6 an output that takes part
// in a feedback arc is emitted as wire<N> while most are value<N>.
struct CxxMember { std::string kind; int width; };

// Wide-signal support: signals up to 64 bits use uint64_t (byte-identical to the
// pre-wide codegen); 65..128 bits use unsigned __int128 so big datapath chunks
// (e.g. dec's 68-bit i0_brp) compute correctly instead of truncating to 64.
// GSM_U32=1: width-aware carriers — signals <=32 bits are held in uint32_t
// instead of uint64_t.  Semantically neutral on CPU (results are width-
// masked either way; headroom-needing ops cast up explicitly below) but
// ~2.5x on GPU targets, which emulate 64-bit integer ops (measured on the
// fsm_bench farm, RTX 2060).  Default OFF until the differential gates pass.
static bool g_u32 = false;

static const char *ctype(int w) {
    if (g_u32 && w <= 32) return "uint32_t";
    return w > 64 ? "unsigned __int128" : "uint64_t";
}

// Emit a C literal for a value up to 128 bits (hi/lo split for >64); for <=64
// emits exactly the old `UINT64_C(0x..)` spelling so narrow output is unchanged.
static std::string u128_lit(unsigned __int128 v, int w = 64) {
    std::ostringstream o;
    uint64_t lo = (uint64_t)v, hi = (uint64_t)(v >> 64);
    if (g_u32 && w <= 32 && v <= 0xffffffffu) {
        o << "0x" << std::hex << lo << "U";
        return o.str();
    }
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
    if (g_u32 && w <= 32) {
        std::ostringstream o;
        o << "0x" << std::hex << ((UINT64_C(1) << w) - 1) << "U";
        return o.str();
    }
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
// GSM_WIDE64: 64-bit limbs for wide signals (x86-64 native; halves the limb
// ops on 256-bit datapaths).  Default stays 32-bit limbs, byte-identical.
static bool g_w64 = false;
static inline int  nlimbs(int w)  { return g_w64 ? (w + 63) / 64
                                                 : (w + 31) / 32; }
static inline const char *lt() { return g_w64 ? "uint64_t" : "uint32_t"; }

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
"static inline __attribute__((always_inline)) uint64_t wslice64(const uint32_t*s,int off,int w,int n){int l=off>>5,b=off&31;uint64_t lo=s[l];if(l+1<n)lo|=(uint64_t)s[l+1]<<32;uint64_t v=lo>>b;if(b&&w+b>64&&l+2<n)v|=(uint64_t)s[l+2]<<(64-b);return w>=64?v:(v&((UINT64_C(1)<<w)-1));}\n"
"// word-chunked: the bit-at-a-time form cost 24k bit-iterations per network\n"
"// pass on dec-class chunks (282 calls, 268 of them 88 bits wide)\n"
"static inline __attribute__((always_inline)) void wplace(uint32_t*d,int doff,const uint32_t*s,int w){\n"
"  int soff=0;\n"
"  while(w>0){int db=doff&31,sb=soff&31;int n=32-(db>sb?db:sb);if(n>w)n=w;\n"
"    uint32_t m=(n>=32)?0xffffffffu:((1u<<n)-1u);\n"
"    d[doff>>5]=(d[doff>>5]&~(m<<db))|(((s[soff>>5]>>sb)&m)<<db);\n"
"    doff+=n;soff+=n;w-=n;}}\n"
"static inline void wplaceb(uint32_t*d,int off,uint32_t b){d[off>>5]=(d[off>>5]&~(1u<<(off&31)))|((b&1)<<(off&31));}\n"
"static inline __attribute__((always_inline)) void worbits(uint32_t*d,int doff,const uint32_t*s,int soff,int w){\n"
"  while(w>0){int db=doff&31,sb=soff&31;int n=32-(db>sb?db:sb);if(n>w)n=w;\n"
"    uint32_t m=(n>=32)?0xffffffffu:((1u<<n)-1u);\n"
"    d[doff>>5]|=((s[soff>>5]>>sb)&m)<<db; doff+=n;soff+=n;w-=n;}}\n"
"static inline __attribute__((always_inline)) void worbits_s(uint32_t*d,int doff,uint64_t s,int soff,int w){\n"
"  s>>=soff;\n"
"  while(w>0){int db=doff&31;int n=32-db;if(n>w)n=w;\n"
"    uint32_t m=(n>=32)?0xffffffffu:((1u<<n)-1u);\n"
"    d[doff>>5]|=((uint32_t)s&m)<<db; s>>=n;doff+=n;w-=n;}}\n"
"static inline __attribute__((always_inline)) void wplacew_s(uint32_t*d,int doff,uint64_t s,int soff,int w){\n"
"  s>>=soff;\n"
"  while(w>0){int db=doff&31;int n=32-db;if(n>w)n=w;\n"
"    uint32_t m=(n>=32)?0xffffffffu:((1u<<n)-1u);\n"
"    d[doff>>5]=(d[doff>>5]&~(m<<db))|(((uint32_t)s&m)<<db);\n"
"    s>>=n;doff+=n;w-=n;}}\n"
"static inline void wplacew(uint32_t*d,int doff,const uint32_t*s,int soff,int w){\n"
"  while(w>0){int db=doff&31,sb=soff&31;int n=32-(db>sb?db:sb);if(n>w)n=w;\n"
"    uint32_t m=(n>=32)?0xffffffffu:((1u<<n)-1u);\n"
"    d[doff>>5]=(d[doff>>5]&~(m<<db))|(((s[soff>>5]>>sb)&m)<<db);\n"
"    doff+=n;soff+=n;w-=n;}}\n"
"\n";

// 64-bit-limb variant (GSM_WIDE64).  Same names/contracts, uint64_t limbs;
// carries and $mul partial products use unsigned __int128.
static const char *WIDE_RT64 =
"// --- wide-int runtime (little-endian uint64_t limbs) ---\n"
"typedef unsigned __int128 w128;\n"
"static inline void wcopy(uint64_t*d,const uint64_t*a,int n){for(int i=0;i<n;i++)d[i]=a[i];}\n"
"static inline void wand(uint64_t*d,const uint64_t*a,const uint64_t*b,int n){for(int i=0;i<n;i++)d[i]=a[i]&b[i];}\n"
"static inline void wor_(uint64_t*d,const uint64_t*a,const uint64_t*b,int n){for(int i=0;i<n;i++)d[i]=a[i]|b[i];}\n"
"static inline void wxor(uint64_t*d,const uint64_t*a,const uint64_t*b,int n){for(int i=0;i<n;i++)d[i]=a[i]^b[i];}\n"
"static inline void wnot(uint64_t*d,const uint64_t*a,int n){for(int i=0;i<n;i++)d[i]=~a[i];}\n"
"static inline void wadd(uint64_t*d,const uint64_t*a,const uint64_t*b,int n){w128 c=0;for(int i=0;i<n;i++){c+=(w128)a[i]+b[i];d[i]=(uint64_t)c;c>>=64;}}\n"
"static inline void wsub(uint64_t*d,const uint64_t*a,const uint64_t*b,int n){w128 c=1;for(int i=0;i<n;i++){c+=(w128)a[i]+(uint64_t)~b[i];d[i]=(uint64_t)c;c>>=64;}}\n"
"static inline void wneg(uint64_t*d,const uint64_t*a,int n){w128 c=1;for(int i=0;i<n;i++){c+=(w128)(uint64_t)~a[i];d[i]=(uint64_t)c;c>>=64;}}\n"
"static inline void wmul(uint64_t*d,const uint64_t*a,const uint64_t*b,int n){uint64_t t[64];for(int i=0;i<n;i++)t[i]=0;for(int i=0;i<n;i++){w128 c=0;for(int j=0;i+j<n;j++){w128 p=(w128)a[i]*b[j]+t[i+j]+c;t[i+j]=(uint64_t)p;c=p>>64;}}for(int i=0;i<n;i++)d[i]=t[i];}\n"
"static inline void wshl(uint64_t*d,const uint64_t*a,int s,int n){int w=s>>6,b=s&63;for(int i=n-1;i>=0;i--){uint64_t v=0;int j=i-w;if(j>=0){v=a[j]<<b;if(b&&j-1>=0)v|=a[j-1]>>(64-b);}d[i]=v;}}\n"
"static inline void wshr(uint64_t*d,const uint64_t*a,int s,int n){int w=s>>6,b=s&63;for(int i=0;i<n;i++){uint64_t v=0;int j=i+w;if(j<n){v=a[j]>>b;if(b&&j+1<n)v|=a[j+1]<<(64-b);}d[i]=v;}}\n"
"static inline int weq(const uint64_t*a,const uint64_t*b,int n){for(int i=0;i<n;i++)if(a[i]!=b[i])return 0;return 1;}\n"
"static inline int wult(const uint64_t*a,const uint64_t*b,int n){for(int i=n-1;i>=0;i--)if(a[i]!=b[i])return a[i]<b[i];return 0;}\n"
"static inline int wslt(const uint64_t*a,const uint64_t*b,int n){uint64_t sa=a[n-1]>>63,sb=b[n-1]>>63;if(sa!=sb)return (int)sa;return wult(a,b,n);}\n"
"static inline int wred_or(const uint64_t*a,int n){for(int i=0;i<n;i++)if(a[i])return 1;return 0;}\n"
"static inline int wred_xor(const uint64_t*a,int n){uint64_t x=0;for(int i=0;i<n;i++)x^=a[i];x^=x>>32;x^=x>>16;x^=x>>8;x^=x>>4;x^=x>>2;x^=x>>1;return (int)(x&1);}\n"
"static inline int wred_and(const uint64_t*a,int width,int n){for(int i=0;i<n;i++){uint64_t m=(i==n-1&&(width&63))?((UINT64_C(1)<<(width&63))-1):~UINT64_C(0);if((a[i]&m)!=m)return 0;}return 1;}\n"
"static inline __attribute__((always_inline)) uint64_t wslice64(const uint64_t*s,int off,int w,int n){int l=off>>6,b=off&63;uint64_t v=s[l]>>b;if(b&&l+1<n)v|=s[l+1]<<(64-b);return w>=64?v:(v&((UINT64_C(1)<<w)-1));}\n"
"static inline __attribute__((always_inline)) void wplace(uint64_t*d,int doff,const uint64_t*s,int w){\n"
"  int soff=0;\n"
"  while(w>0){int db=doff&63,sb=soff&63;int n=64-(db>sb?db:sb);if(n>w)n=w;\n"
"    uint64_t m=(n>=64)?~UINT64_C(0):((UINT64_C(1)<<n)-1);\n"
"    d[doff>>6]=(d[doff>>6]&~(m<<db))|(((s[soff>>6]>>sb)&m)<<db);\n"
"    doff+=n;soff+=n;w-=n;}}\n"
"static inline void wplaceb(uint64_t*d,int off,uint64_t b){d[off>>6]=(d[off>>6]&~(UINT64_C(1)<<(off&63)))|((b&1)<<(off&63));}\n"
"static inline __attribute__((always_inline)) void worbits(uint64_t*d,int doff,const uint64_t*s,int soff,int w){\n"
"  while(w>0){int db=doff&63,sb=soff&63;int n=64-(db>sb?db:sb);if(n>w)n=w;\n"
"    uint64_t m=(n>=64)?~UINT64_C(0):((UINT64_C(1)<<n)-1);\n"
"    d[doff>>6]|=((s[soff>>6]>>sb)&m)<<db; doff+=n;soff+=n;w-=n;}}\n"
"static inline __attribute__((always_inline)) void worbits_s(uint64_t*d,int doff,uint64_t s,int soff,int w){\n"
"  s>>=soff;\n"
"  while(w>0){int db=doff&63;int n=64-db;if(n>w)n=w;\n"
"    uint64_t m=(n>=64)?~UINT64_C(0):((UINT64_C(1)<<n)-1);\n"
"    d[doff>>6]|=(s&m)<<db; if(n<64)s>>=n; else s=0; doff+=n;w-=n;}}\n"
"static inline __attribute__((always_inline)) void wplacew_s(uint64_t*d,int doff,uint64_t s,int soff,int w){\n"
"  s>>=soff;\n"
"  while(w>0){int db=doff&63;int n=64-db;if(n>w)n=w;\n"
"    uint64_t m=(n>=64)?~UINT64_C(0):((UINT64_C(1)<<n)-1);\n"
"    d[doff>>6]=(d[doff>>6]&~(m<<db))|((s&m)<<db);\n"
"    if(n<64)s>>=n; else s=0; doff+=n;w-=n;}}\n"
"static inline void wplacew(uint64_t*d,int doff,const uint64_t*s,int soff,int w){\n"
"  while(w>0){int db=doff&63,sb=soff&63;int n=64-(db>sb?db:sb);if(n>w)n=w;\n"
"    uint64_t m=(n>=64)?~UINT64_C(0):((UINT64_C(1)<<n)-1);\n"
"    d[doff>>6]=(d[doff>>6]&~(m<<db))|(((s[soff>>6]>>sb)&m)<<db);\n"
"    doff+=n;soff+=n;w-=n;}}\n"
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
        return u128_lit(v, val.size());
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
            part = u128_lit(v, chunk.data.size());
        }
        if (pos == 0)
            expr = part;
        else {
            std::ostringstream oss;
            // A u32-carried part shifted by pos >= 32 would be UB; cast up
            // whenever the accumulating value exceeds 32 bits under GSM_U32
            if (g_u32 && pos + chunk.width > 32)
                oss << "(((uint64_t)(" << part << ") << " << std::dec << pos
                    << ") | " << expr << ")";
            else
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
    if (g_u32 && width <= 32)   // u32 carrier needs explicit headroom
        oss << "((int64_t)(((uint64_t)(" << expr << ")) << " << (64 - width)
            << ") >> " << (64 - width) << ")";
    else
        oss << "((int64_t)((" << expr << ") << " << (64 - width) << ") >> "
            << (64 - width) << ")";
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
    if (g_w64) {
        if (width & 63)
            fprintf(o, "      %s[%d] &= UINT64_C(0x%llx);\n", y.c_str(), ny - 1,
                    (unsigned long long)((UINT64_C(1) << (width & 63)) - 1));
        return;
    }
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
    std::vector<uint64_t> cacc(ny, 0);
    const int LS = g_w64 ? 6 : 5, LM = g_w64 ? 63 : 31;
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
            // PEEPHOLE: fold a single-word bit-range OR into one statement.
            //
            // pos, off and w are all COMPILE-TIME CONSTANTS here, so whenever
            // the range lands inside one 32-bit word on both sides, worbits'
            // entire `while` loop collapses to
            //     d[K] |= ((s[J] >> SB) & MASK) << DB;
            // with every operand an immediate.  Emitting that directly instead
            // of a call matters far more than it looks:
            //
            // MEASURED on wide_n8w1024 (perf, 200k cycles): worbits 58.7% +
            // worbits_s 12.5% = 71% OF ALL INSTRUCTIONS in the accelerated run,
            // against 10% for sm_clock_out -- the actual model.  The width
            // distribution is why: of ~5,500 call sites in that design, 2,754
            // copy ONE BIT, 1,314 copy two, 750 three, 303 four, and only 202
            // are 1024 wide.  A one-bit copy was paying a function call, loop
            // setup and a runtime mask computation.
            //
            // `static inline` did NOT save us: with thousands of call sites gcc
            // declines to inline at any -O level (worbits appears as a real
            // symbol in the .so), which is exactly why wplace got constprop
            // clones and worbits got none.  Emitting the folded form removes
            // gcc's discretion instead of hoping for it.
            //
            // Wide copies (w=1024) keep the call: the loop is already one
            // word-op per 32 bits there, which is what we want.
            const int dw = pos & 31, sw = off & 31;
            const bool one_word_dst = !g_w64 && dw + w <= 32;
            if (is_wide(chunk.wire->width)) {
                if (one_word_dst && sw + w <= 32) {
                    const unsigned mask =
                        (w >= 32) ? 0xffffffffu : ((1u << w) - 1u);
                    fprintf(o, "      %s[%d] |= ((%s[%d] >> %d) & 0x%xu) << %d;\n",
                            dst.c_str(), pos >> 5, wn.c_str(), off >> 5,
                            sw, mask, dw);
                }
                else
                    fprintf(o, "      worbits(%s,%d,%s,%d,%d);\n",
                            dst.c_str(), pos, wn.c_str(), off, w);
            }
            else {
                // scalar source: a uint64 value, shifted then masked
                if (one_word_dst && off + w <= 64) {
                    const unsigned mask =
                        (w >= 32) ? 0xffffffffu : ((1u << w) - 1u);
                    fprintf(o, "      %s[%d] |= ((uint32_t)((%s) >> %d) & 0x%xu) << %d;\n",
                            dst.c_str(), pos >> 5, wn.c_str(), off, mask, dw);
                }
                else
                    fprintf(o, "      worbits_s(%s,%d,%s,%d,%d);\n",
                            dst.c_str(), pos, wn.c_str(), off, w);
            }
        } else {
            any_const = true;
            for (int i = 0; i < w; i++) {
                bool bit = (i < (int)chunk.data.size() && chunk.data[i] == RTLIL::S1);
                if (bit) { int p = pos + i; if ((p >> LS) < ny) cacc[p >> LS] |= UINT64_C(1) << (p & LM); }
            }
        }
        pos += w;
    }
    if (any_const)
        for (int i = 0; i < ny; i++)
            if (cacc[i]) {
                if (g_w64)
                    fprintf(o, "      %s[%d]|=UINT64_C(0x%llx);\n", dst.c_str(), i,
                            (unsigned long long)cacc[i]);
                else
                    fprintf(o, "      %s[%d]|=0x%xu;\n", dst.c_str(), i,
                            (unsigned)cacc[i]);
            }
    if (signext && sext_w >= 1 && g_w64) {
        int sb = sext_w - 1, tl = sb >> 6;
        uint64_t hm = (sb & 63) == 63 ? 0 : (~UINT64_C(0) << ((sb & 63) + 1));
        fprintf(o, "      if(%s[%d]&(UINT64_C(1)<<%d)){", dst.c_str(), tl, sb & 63);
        if (hm) fprintf(o, " %s[%d]|=UINT64_C(0x%llx);", dst.c_str(), tl,
                        (unsigned long long)hm);
        for (int i = tl + 1; i < ny; i++)
            fprintf(o, " %s[%d]=~UINT64_C(0);", dst.c_str(), i);
        fprintf(o, " }\n");
    } else if (signext && sext_w >= 1) {
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
    // Y may be a CONCATENATION of several (possibly non-contiguous, possibly
    // multi-wire) slices — e.g. {din[61],din[58],din[54:35],din[32:19],din[17:0]}.
    // yoff/yw describe only the FIRST chunk, so a single wplace(y,yoff,_wy,yw)
    // packs the yw-bit result contiguously at yoff and MIS-PLACES every later
    // slice (dec's writeback packet shifted/dropped). Collect the chunks
    // (LSB-first) and, when there are several, scatter each to its own wire+offset
    // just like the scalar path's _yspl scatter does.
    std::vector<RTLIL::SigChunk> ychunks;
    if (cell->hasPort(ID::Y)) {
        auto yy = cell->getPort(ID::Y);
        ychunks.assign(yy.chunks().begin(), yy.chunks().end());
    }
    const bool y_scatter = ychunks.size() > 1;
    // Store the yw-bit result held in _wy (ng limbs) into the target.
    auto put_val = [&](int ng) {
        if (y_scatter) {
            int pos = 0;
            for (auto &ch : ychunks) {
                if (ch.wire) {
                    std::string wn = cname(ch.wire->name.str());
                    int w = ch.width, off = ch.offset, ww = ch.wire->width;
                    if (is_wide(ww))   // chunk targets a limb-array wire
                        fprintf(o, "      wplacew(%s,%d,_wy,%d,%d);\n",
                                wn.c_str(), off, pos, w);
                    else if (off == 0 && w == ww)
                        fprintf(o, "      %s = wslice64(_wy,%d,%d,%d);\n",
                                wn.c_str(), pos, w, ng);
                    else
                        fprintf(o, "      %s = (%s & ~(%s << %d)) |"
                                " ((wslice64(_wy,%d,%d,%d) & %s) << %d);\n",
                                wn.c_str(), wn.c_str(), mask_lit(w).c_str(), off,
                                pos, w, ng, mask_lit(w).c_str(), off);
                }
                pos += ch.width;
            }
        }
        else if (twide) fprintf(o, "      wplace(%s,%d,_wy,%d);\n", y.c_str(), yoff, yw);
        else            fprintf(o, "      %s = wslice64(_wy,0,%d,%d);\n", y.c_str(), yw, ng); };
    // Store a 1-bit result expression into the target.
    auto put_bit = [&](const std::string &e) {
        if (twide) fprintf(o, "      wplaceb(%s,%d,%s);\n", y.c_str(), yoff, e.c_str());
        else       fprintf(o, "      %s = %s;\n", y.c_str(), e.c_str()); };

    fprintf(o, "    {\n");
    if (type == "$add" || type == "$sub" || type == "$mul" ||
        type == "$and" || type == "$or" || type == "$xor") {
        int ng = nlimbs(std::max(yw, std::max(aw, bw)));
        fprintf(o, "      %s _wa[%d],_wb[%d],_wy[%d];\n", lt(), ng, ng, ng);
        matA("_wa", ng, false, 0); matB("_wb", ng, false, 0);
        const char *fn = type == "$add" ? "wadd" : type == "$sub" ? "wsub" :
                         type == "$mul" ? "wmul" : type == "$and" ? "wand" :
                         type == "$or" ? "wor_" : "wxor";
        fprintf(o, "      %s(_wy,_wa,_wb,%d);\n", fn, ng);
        put_val(ng);
    } else if (type == "$xnor") {
        int ng = nlimbs(std::max(yw, std::max(aw, bw)));
        fprintf(o, "      %s _wa[%d],_wb[%d],_wy[%d];\n", lt(), ng, ng, ng);
        matA("_wa", ng, false, 0); matB("_wb", ng, false, 0);
        fprintf(o, "      wxor(_wy,_wa,_wb,%d); wnot(_wy,_wy,%d);\n", ng, ng);
        put_val(ng);
    } else if (type == "$not" || type == "$neg") {
        int ng = nlimbs(std::max(yw, aw));
        fprintf(o, "      %s _wa[%d],_wy[%d];\n", lt(), ng, ng);
        matA("_wa", ng, false, 0);
        fprintf(o, "      %s(_wy,_wa,%d);\n", type == "$not" ? "wnot" : "wneg", ng);
        put_val(ng);
    } else if (type == "$shl" || type == "$shr") {
        int ng = nlimbs(std::max(yw, aw));
        fprintf(o, "      %s _wa[%d],_wy[%d]; int _sh=(int)(%s);\n",
                lt(), ng, ng, scalar_of(cell->getPort(ID::B), sigmap).c_str());
        matA("_wa", ng, false, 0);
        fprintf(o, "      %s(_wy,_wa,_sh,%d);\n", type == "$shl" ? "wshl" : "wshr", ng);
        put_val(ng);
    } else if (type == "$shift" || type == "$shiftx") {
        // Dynamic indexed part-select on a WIDE operand: Y = A >> B, where B is a
        // (possibly signed) offset; negative B shifts LEFT by -B. yosys emits these
        // for signal-indexed part-selects — critically, VeeR models each icache
        // SRAM as one flat wire[8703:0] ram_core with a dynamic $shiftx read
        // (ram_core[adr +: w]) and a $shift one-hot fill-word placement on write.
        // Previously unhandled here -> fell through to the // TODO unhandled-WIDE
        // stub, leaving Y at 0: every icache HIT read returned 0 -> corrupt
        // IC_RD_DATA -> boot derail. x/undefined fill = 0 in 2-state; put_val()
        // masks Y to yw. Mirrors the scalar handler + the $shl/$shr wide case above.
        int ng = nlimbs(std::max(yw, aw));
        bool bs = cell->getParam(ID::B_SIGNED).as_bool();
        fprintf(o, "      %s _wa[%d],_wy[%d]; int64_t _sh=(int64_t)(uint64_t)(%s);\n",
                lt(), ng, ng, scalar_of(cell->getPort(ID::B), sigmap).c_str());
        if (bs) {
            int bw = cell->getPort(ID::B).size();
            if (bw < 64)
                fprintf(o, "      if (_sh & (INT64_C(1)<<%d)) _sh -= (INT64_C(1)<<%d);\n",
                        bw - 1, bw);
        }
        matA("_wa", ng, false, 0);
        fprintf(o, "      if (_sh >= 0) wshr(_wy,_wa,(int)_sh,%d); else wshl(_wy,_wa,(int)(-_sh),%d);\n",
                ng, ng);
        put_val(ng);
    } else if (type == "$mux") {
        fprintf(o, "      %s _wa[%d],_wb[%d],_wy[%d];\n", lt(), ny, ny, ny);
        matA("_wa", ny, false, 0); matB("_wb", ny, false, 0);
        fprintf(o, "      if (%s) wcopy(_wy,_wb,%d); else wcopy(_wy,_wa,%d);\n",
                scalar_of(cell->getPort(ID::S), sigmap).c_str(), ny, ny);
        put_val(ny);
    } else if (type == "$pmux") {
        int n_cases = cell->getPort(ID::S).size();
        fprintf(o, "      %s _wy[%d];\n", lt(), ny);
        emit_materialize(o, "_wy", ny, cell->getPort(ID::A), sigmap, false, 0);
        for (int i = 0; i < n_cases; i++) {
            fprintf(o, "      if (%s) {\n",
                    scalar_of(cell->getPort(ID::S).extract(i, 1), sigmap).c_str());
            emit_materialize(o, "_wy", ny, cell->getPort(ID::B).extract(i * yw, yw), sigmap, false, 0);
            fprintf(o, "      }\n");
        }
        put_val(ny);
    } else if (type == "$eq" || type == "$ne"
               || type == "$eqx" || type == "$nex") {
        // $eqx/$nex (===/!==, casez compares) are exact 4-state equality; in
        // 2-state they reduce to plain equality.
        const bool is_eq = type == "$eq" || type == "$eqx";
        int nc = nlimbs(std::max(aw, bw));
        fprintf(o, "      %s _wa[%d],_wb[%d];\n", lt(), nc, nc);
        matA("_wa", nc, false, 0); matB("_wb", nc, false, 0);
        char e[64]; snprintf(e, sizeof e, "weq(_wa,_wb,%d)?%d:%d", nc,
                             is_eq ? 1 : 0, is_eq ? 0 : 1);
        put_bit(e);
    } else if (type == "$lt" || type == "$le" || type == "$gt" || type == "$ge") {
        bool sg = is_signed(cell);
        int nc = nlimbs(std::max(aw, bw));
        fprintf(o, "      %s _wa[%d],_wb[%d];\n", lt(), nc, nc);
        matA("_wa", nc, sg, aw); matB("_wb", nc, sg, bw);
        const char *cmp = sg ? "wslt" : "wult";
        char e[64];
        if (type == "$lt")      snprintf(e, sizeof e, "%s(_wa,_wb,%d)?1:0", cmp, nc);
        else if (type == "$gt") snprintf(e, sizeof e, "%s(_wb,_wa,%d)?1:0", cmp, nc);
        else if (type == "$le") snprintf(e, sizeof e, "%s(_wb,_wa,%d)?0:1", cmp, nc);
        else                    snprintf(e, sizeof e, "%s(_wa,_wb,%d)?0:1", cmp, nc);
        put_bit(e);
    } else if ((type == "$logic_and" || type == "$logic_or")
               && aw <= 64 && bw <= 64) {
        // narrow logical op landing in a wide target (partial drive)
        std::string e = "((" + scalar_of(cell->getPort(ID::A), sigmap)
            + " != 0) " + (type == "$logic_and" ? "&&" : "||") + " ("
            + scalar_of(cell->getPort(ID::B), sigmap) + " != 0)) ? 1 : 0";
        put_bit(e);
    } else if (type == "$reduce_or" || type == "$reduce_bool" || type == "$logic_not") {
        int na = nlimbs(aw);
        fprintf(o, "      %s _wa[%d];\n", lt(), na); matA("_wa", na, false, 0);
        char e[48]; snprintf(e, sizeof e, "wred_or(_wa,%d)?%d:%d", na,
                             type == "$logic_not" ? 0 : 1, type == "$logic_not" ? 1 : 0);
        put_bit(e);
    } else if (type == "$reduce_and") {
        int na = nlimbs(aw);
        fprintf(o, "      %s _wa[%d];\n", lt(), na); matA("_wa", na, false, 0);
        char e[48]; snprintf(e, sizeof e, "wred_and(_wa,%d,%d)?1:0", aw, na);
        put_bit(e);
    } else if (type == "$reduce_xor") {
        int na = nlimbs(aw);
        fprintf(o, "      %s _wa[%d];\n", lt(), na); matA("_wa", na, false, 0);
        char e[48]; snprintf(e, sizeof e, "wred_xor(_wa,%d)?1:0", na);
        put_bit(e);
    } else {
        fprintf(stderr, "gen_statemachine: unhandled WIDE cell type %s"
                " (yw=%d aw=%d bw=%d) — declining (a silent stub would leave"
                " its output 0 and miscompute)\n", type.c_str(), yw, aw, bw);
        exit(1);   // install checks the exit code; the chunk stays interpreted
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

// --- GSM_ICG2EN: ICG (latch+AND gated clock) -> clock-enable rewrite --------
// A flattened wrapper that internalizes an integrated clock gate (transparent-
// low latch on the enable + AND(clk, en_l) driving downstream flop clocks)
// declines at the extra-clock-must-be-a-module-input gate: the gated clock is
// an INTERNAL net.  With GSM_ICG2EN=1 each such cone is matched structurally
// and rewritten to its synchronous equivalent:
//     $dff on AND(clkin, latch(en))   ->   $dffe on clkin, EN = latch cone
// Soundness: a transparent-LOW latch tracks D while clk is low and freezes at
// the rise, so its value AT the posedge equals the pre-edge value of its D
// cone -- exactly what emit_seq's commit reads (comb evaluated on pre-edge
// state).  A transparent-HIGH latch is NOT equivalent -> decline.  Anything
// unmatched keeps its internal clock and still hits the extra-clock decline,
// so a partial match can only fail SAFE (the chunk stays interpreted).
// If the gated clock is also a module OUTPUT (interp consumers clock on it),
// the raw enable must NOT leak through mid-cycle (an enable rising during
// clk-high would publish a spurious posedge).  Moore-ize: a hidden 1-bit
// $dff commits the enable cone at each base posedge (== the value the ICG
// latch froze), and the exported AND reads sm_icg_clkval -- the real clock
// value poked by the bridge before each eval (the comb-local _clk is pinned
// 0 by design; inputs_t deliberately excludes the primary clock).
// --- GSM_ACTIVITY_CENSUS: per-instance-group output-change census -------
// Diagnostic mode: everything still evaluates (correctness-neutral); each
// comb cell's result is folded into its top-level-instance group's hash,
// and at the end of every sm_comb call each group's hash is compared with
// the previous call's — a group whose hash is unchanged is a group an
// activity-gated build could have skipped.  Report at exit.  This measures
// the GATING CEILING on real workloads before building the transform.
static bool g_census = false;
static bool g_census_active = false;

// --- GSM_GATED: activity-gated block evaluation --------------------------
// Cells are partitioned into contiguous topo-order blocks of K.  A block is
// evaluated only when its dirty bit is set.  Boundary nets (read outside
// their writer block, or by the seq phase) persist as file-scope statics so
// a skipped block leaves its last-computed values readable; ungated
// functions (sm_comb outcone, sm_clock_out, sm_dump_comb) re-declare every
// net as a local, which SHADOWS the statics — they keep today's semantics
// untouched.  Dirty sources: primary-input compares (entry), boundary-net
// change compares (after a block runs), register commit compares (post-seq,
// against a persistent prev copy so async-reset transitions count too), and
// memory-write execution (conservative, under the write enable).
static bool g_gated = false;
static int  g_gated_block = 32;
// GSM_COMB_SPLIT=K: emit sm_clock's comb network as K part functions plus a
// commit function, cross-part wires carried in an xw_state_t struct.  Restores
// register-allocator locality on GPU (one 24k-cell sm_comb spilled ~7x worse
// per eval than a half-size one on sm_75).  Single-clock, ungated only.
static int  g_comb_split = 0;
static bool g_emit_gated_body = false;
struct GatedPlan {
    int nb = 0, nw = 0;
    std::map<Yosys::RTLIL::Cell*,int> blk;
    std::set<std::string> boundary;                    // persist as statics
    std::map<std::string,int> bwidth;                  // boundary net width
    std::map<std::string,std::set<int>> readers;       // net -> reader blocks
    std::map<std::string,std::set<int>> writers;       // net -> writer blocks
                                                       // (slice writebacks give
                                                       // one net several)
    std::vector<std::vector<std::string>> blk_cmp;     // per block: nets to compare
    std::map<std::string,std::set<int>> memreaders;    // memid -> reader blocks
};
static GatedPlan g_gp;
// OR the reader-set (minus exclude) into the emitted dirty words.
static std::string gd_or_str(const std::set<int> &rd)
{
    std::map<int,uint64_t> wm;
    for (int b : rd) wm[b>>6] |= 1ull<<(b&63);
    std::string r;
    char buf[64];
    for (auto &p : wm) {
        snprintf(buf, sizeof buf, " _bd[%d] |= UINT64_C(0x%llx);", p.first,
                 (unsigned long long)p.second);
        r += buf;
    }
    return r;
}
static void gd_emit_or(FILE *out, const std::set<int> &rd, int exclude,
                       const char *ind)
{
    std::map<int,uint64_t> wm;
    for (int b : rd) { if (b == exclude) continue; wm[b>>6] |= 1ull<<(b&63); }
    for (auto &p : wm)
        fprintf(out, "%s_bd[%d] |= UINT64_C(0x%llx);\n", ind, p.first,
                (unsigned long long)p.second);
}
static std::map<std::string,unsigned> g_cns_salt;
static unsigned cns_salt_of(RTLIL::Cell *cell) {
    auto it = g_cns_salt.find(cell->name.str());
    if (it != g_cns_salt.end()) return it->second;
    unsigned v = (unsigned)g_cns_salt.size() * 2654435761u + 0x9e3779b9u;
    g_cns_salt[cell->name.str()] = v;
    return v;
}
static std::vector<std::string> g_cns_groups;
static std::map<std::string,int> g_cns_gid;
static int g_census_depth = 1;
static std::string cns_sanitize(const std::string &raw)
{
    std::string n;
    for (size_t i = 0; i < raw.size(); i++) {
        if (raw.compare(i, 9, "$flatten\\") == 0) { i += 8; continue; }
        if (raw.compare(i, 8, "flatten\\") == 0 && (i == 0 || raw[i-1] == '.'))
            { i += 7; continue; }
        if (raw[i] != '\\') n += raw[i];
    }
    if (!n.empty() && n[0]=='$') n = n.substr(1);
    return n;
}
static int g_census_block = 0;   // >0: group by topo-order block of K cells
static std::map<std::string,int> g_cns_gid_override;
static int cns_gid_of(RTLIL::Cell *cell)
{
    if (g_census_block > 0) {
        auto ov = g_cns_gid_override.find(cell->name.str());
        if (ov != g_cns_gid_override.end()) return ov->second;
    }
    // Sanitize: drop yosys flatten\ prefixes and stray backslashes (they
    // would be interpreted as escapes inside the emitted C string literal),
    // then group by the first g_census_depth '.'-components.
    std::string n = cns_sanitize(cell->name.str());
    // depth <= 0 means per-cell (group = full sanitized name); otherwise
    // group by the first depth '.'-components, or the whole name if the
    // hierarchy is shallower than that.
    std::string g;
    if (g_census_depth <= 0)
        g = n;
    else {
        size_t d = 0;
        for (int k = 0; k < g_census_depth && d != std::string::npos; k++)
            d = n.find('.', k ? d + 1 : 0);
        g = (d == std::string::npos) ? n : n.substr(0, d);
    }
    auto it = g_cns_gid.find(g);
    if (it != g_cns_gid.end()) return it->second;
    int id = (int)g_cns_groups.size();
    g_cns_groups.push_back(g);
    g_cns_gid[g] = id;
    return id;
}

static bool g_icg2en_used = false;   // any cone rewritten (scan-json guard)
static bool g_icg2en_out  = false;   // an exported cone needs sm_icg_clkval
static void icg2en_rewrite(RTLIL::Module *mod)
{
    SigMap smap(mod);
    dict<RTLIL::SigBit, RTLIL::Cell*> drv;
    for (auto &cp : mod->cells_) {
        RTLIL::Cell *c = cp.second;
        for (auto &conn : c->connections())
            if (c->output(conn.first))
                for (auto b : smap(conn.second))
                    if (b.wire) drv[b] = c;
    }
    auto is_ff = [](RTLIL::Cell *c) {
        return c->type.in(ID($dff), ID($adff), ID($dffe), ID($adffe),
                          ID($sdff), ID($sdffe));
    };
    // FF cells grouped by their canonical INTERNAL clock bit
    dict<RTLIL::SigBit, std::vector<RTLIL::Cell*>> groups;
    for (auto &cp : mod->cells_) {
        RTLIL::Cell *c = cp.second;
        if (!is_ff(c) || !c->hasPort(ID::CLK)) continue;
        RTLIL::SigBit g = smap(c->getPort(ID::CLK))[0];
        if (!g.wire || g.wire->port_input) continue;   // already legal
        groups[g].push_back(c);
    }
    // Clocked memory ports on the same gated clocks (true-memory RAM
    // primitives inside an ICG cone: the way-data $memwr inherits the way
    // clock).  Reclocked alongside the FFs: CLK -> base, EN &= enable.
    dict<RTLIL::SigBit, std::vector<RTLIL::Cell*>> memgroups;
    for (auto &cp : mod->cells_) {
        RTLIL::Cell *c = cp.second;
        bool ismw = c->type.str().compare(0, 6, "$memwr") == 0;
        bool ismr = c->type.str().compare(0, 6, "$memrd") == 0;
        if (!ismw && !ismr) continue;
        if (!c->hasPort(ID::CLK)) continue;
        if (c->hasParam(ID(CLK_ENABLE)) && !c->getParam(ID(CLK_ENABLE)).as_bool())
            continue;                                   // comb read port
        RTLIL::SigBit g = smap(c->getPort(ID::CLK))[0];
        if (!g.wire || g.wire->port_input) continue;
        memgroups[g].push_back(c);
    }
    if (groups.empty()) return;

    int nhold = 0;
    pool<RTLIL::Cell*> latches_connected;
    for (auto &gp : groups) {
        RTLIL::SigBit g = gp.first;
        auto note = [&](const char *why) {
            fprintf(stderr, "icg2en: clock cone %s declined (%s)\n",
                    g.wire->name.c_str(), why);
        };
        auto it = drv.find(g);
        if (it == drv.end()) { note("no driver"); continue; }
        RTLIL::Cell *gate = it->second;
        if (!gate->type.in(ID($and), ID($logic_and))) {
            note("driver is not an AND"); continue; }
        if (GetSize(gate->getPort(ID::Y)) != 1 ||
            GetSize(gate->getPort(ID::A)) != 1 ||
            GetSize(gate->getPort(ID::B)) != 1) {
            note("AND is not 1-bit"); continue; }

        // Latch matcher: q must be the Q of a 1-bit $dlatch transparent
        // exactly when THIS cone's base clock is LOW.
        RTLIL::SigBit clkbit;
        auto match_latch = [&](RTLIL::SigBit q) -> RTLIL::Cell* {
            auto d = drv.find(q);
            if (d == drv.end() || d->second->type != ID($dlatch))
                return nullptr;
            RTLIL::Cell *l = d->second;
            if (l->getParam(ID(WIDTH)).as_int() != 1) return nullptr;
            bool pol = l->getParam(ID(EN_POLARITY)).as_bool();
            RTLIL::SigBit le = smap(l->getPort(ID::EN))[0];
            if (le == clkbit)                    // EN=clk: transparent at 0?
                return pol ? nullptr : l;
            auto ld = drv.find(le);              // EN=!clk: transparent at 1?
            if (ld != drv.end()
                && ld->second->type.in(ID($not), ID($logic_not))
                && smap(ld->second->getPort(ID::A))[0] == clkbit)
                return pol ? l : nullptr;
            return nullptr;
        };

        // Classify AND operands (either order): one directly a module-input
        // bit (the base clock -- v1: no buffer/cascade chase), the other the
        // enable: a matched latch Q, or OR(latch Q, input/const) for the
        // TE-bypass header style (TE assumed quasi-static; note emitted).
        RTLIL::SigBit a = smap(gate->getPort(ID::A))[0];
        RTLIL::SigBit b = smap(gate->getPort(ID::B))[0];
        RTLIL::Cell *latch = nullptr;
        RTLIL::SigBit enbit;
        for (int ord = 0; ord < 2 && latch == nullptr; ord++) {
            RTLIL::SigBit co = ord ? b : a, eo = ord ? a : b;
            if (!co.wire || !co.wire->port_input) continue;
            clkbit = co;
            latch = match_latch(eo);
            if (latch) { enbit = eo; break; }
            auto od = drv.find(eo);
            if (od != drv.end()
                && od->second->type.in(ID($or), ID($logic_or))
                && GetSize(od->second->getPort(ID::Y)) == 1
                && GetSize(od->second->getPort(ID::A)) == 1
                && GetSize(od->second->getPort(ID::B)) == 1) {
                RTLIL::SigBit oa = smap(od->second->getPort(ID::A))[0];
                RTLIL::SigBit ob = smap(od->second->getPort(ID::B))[0];
                auto inpc = [&](RTLIL::SigBit x) {
                    return !x.wire || x.wire->port_input; };
                if ((latch = match_latch(oa)) != nullptr && inpc(ob)) {
                    enbit = eo; break; }
                if ((latch = match_latch(ob)) != nullptr && inpc(oa)) {
                    enbit = eo; break; }
                latch = nullptr;
            }
        }
        if (latch == nullptr) {
            note("no transparent-low latch+AND ICG shape"); continue; }

        // Every flop on this gated clock must be a plain posedge $dff/$adff
        // (post-dffunmap there is no EN to merge; anything else declines).
        bool ffbad = false;
        for (RTLIL::Cell *c : gp.second)
            if (!c->type.in(ID($dff), ID($adff))
                || (c->hasParam(ID(CLK_POLARITY))
                    && !c->getParam(ID(CLK_POLARITY)).as_bool())) {
                ffbad = true; break; }
        if (ffbad) { note("flop is not a posedge $dff/$adff"); continue; }

        // Enable-cone guard: walk enbit's cone (through THE latch via its D).
        // Registers/inputs are sound pre-edge reads; the base clock as a leaf,
        // any other stateful cell, or an oversized cone -> decline.
        bool bad = false;
        {
            // Bound the walk, not the semantics: soundness is the leaf checks
            // (clk-in-cone / stateful cells), and real enable cones (VeeR
            // stall trees) run to thousands of bits.  97/97 EH1-asic cones
            // declined at 512.
            static int bmax = -1;
            if (bmax < 0) {
                const char *e = getenv("GSM_ICG2EN_BUDGET");
                bmax = e ? atoi(e) : 100000;
            }
            int budget = bmax;
            pool<RTLIL::SigBit> seen;
            std::vector<RTLIL::SigBit> q{ enbit };
            while (!q.empty()) {
                RTLIL::SigBit x = smap(q.back()); q.pop_back();
                if (!x.wire || seen.count(x)) continue;
                // budget counts UNIQUE visited bits (a dense cone re-pushes
                // the same bits many times; charging per pop mis-declined
                // real stall-tree enables at any sane limit)
                if (--budget < 0) { note("enable cone too large"); bad = true; break; }
                seen.insert(x);
                if (x == clkbit) {
                    note("base clock read inside enable cone"); bad = true; break; }
                if (x.wire->port_input) continue;
                auto dd = drv.find(x);
                if (dd == drv.end()) continue;
                RTLIL::Cell *c = dd->second;
                if (c == latch) { q.push_back(c->getPort(ID::D)[0]); continue; }
                if (is_ff(c)) continue;
                if (c->type.in(ID($dlatch), ID($adlatch), ID($dlatchsr),
                               ID($sr), ID($aldff), ID($aldffe))
                    || c->type.str().compare(0, 6, "$memrd") == 0) {
                    note("stateful cell inside enable cone"); bad = true; break; }
                for (auto &conn : c->connections())
                    if (c->input(conn.first))
                        for (auto bb : smap(conn.second))
                            if (bb.wire) q.push_back(bb);
            }
        }
        if (bad) continue;

        // Fanout audit: the gated net may feed ONLY this group's CLK pins and
        // module outputs.  Any other reader would see the zeroed comb _clk ->
        // decline (today's behavior, structurally safe).
        bool exported = false;
        for (auto &cp : mod->cells_) {
            RTLIL::Cell *c = cp.second;
            if (c == gate || bad) continue;
            for (auto &conn : c->connections()) {
                if (!c->input(conn.first)) continue;
                for (auto bb : smap(conn.second)) {
                    if (bb != g) continue;
                    bool member = false;
                    for (RTLIL::Cell *m : gp.second)
                        if (m == c) { member = true; break; }
                    if (!member && memgroups.count(g))
                        for (RTLIL::Cell *m : memgroups.at(g))
                            if (m == c) { member = true; break; }
                    if (!(member && conn.first == ID::CLK)) bad = true;
                }
            }
        }
        if (bad) { note("gated clock has non-clock readers"); continue; }
        for (auto &wp : mod->wires_) {
            if (!wp.second->port_output) continue;
            for (auto bb : smap(RTLIL::SigSpec(wp.second)))
                if (bb == g) { exported = true; break; }
        }

        // ---- rewrite ----
        for (RTLIL::Cell *c : gp.second) {
            c->setPort(ID::CLK, RTLIL::SigSpec(clkbit));
            c->setPort(ID::EN, RTLIL::SigSpec(enbit));
            c->setParam(ID(EN_POLARITY), RTLIL::Const(1, 1));
            c->type = (c->type == ID($adff)) ? ID($adffe) : ID($dffe);
            fprintf(stderr, "icg2en: reg %s reclocked to %s (en=%s)\n",
                    c->name.c_str(), clkbit.wire->name.c_str(),
                    enbit.wire ? enbit.wire->name.c_str() : "const");
        }
        if (memgroups.count(g)) {
            for (RTLIL::Cell *c : memgroups.at(g)) {
                c->setPort(ID::CLK, RTLIL::SigSpec(clkbit));
                RTLIL::SigSpec en = c->getPort(ID::EN);
                RTLIL::SigSpec enrep;
                for (int k2 = 0; k2 < GetSize(en); k2++)
                    enrep.append(enbit);
                RTLIL::SigSpec newen = mod->And(NEW_ID, en, enrep);
                c->setPort(ID::EN, newen);
                fprintf(stderr, "icg2en: mem port %s reclocked to %s (en&=%s)\n",
                        c->name.c_str(), clkbit.wire->name.c_str(),
                        enbit.wire ? enbit.wire->name.c_str() : "const");
            }
        }
        // Latch disposal: transparent-low == wire at every pre-edge read
        // point; the connect keeps its Q net comb-driven for all readers.
        if (!latches_connected.count(latch)) {
            mod->connect(latch->getPort(ID::Q), latch->getPort(ID::D));
            latches_connected.insert(latch);
        }
        if (exported) {
            RTLIL::Wire *hw = mod->addWire(
                RTLIL::IdString(stringf("\\icg2en_hold_%d", nhold++)), 1);
            RTLIL::Const iv(0, 1);
            RTLIL::SigBit lq = latch->getPort(ID::Q)[0];
            if (lq.wire && lq.wire->attributes.count(ID::init)) {
                const RTLIL::Const &wi = lq.wire->attributes.at(ID::init);
                if (GetSize(wi) > lq.offset && wi[lq.offset] == RTLIL::S1)
                    iv = RTLIL::Const(1, 1);
            }
            hw->attributes[ID::init] = iv;
            mod->addDff(NEW_ID, RTLIL::SigSpec(clkbit), RTLIL::SigSpec(enbit),
                        RTLIL::SigSpec(hw), true);
            gate->setPort(ID::A, RTLIL::SigSpec(clkbit));
            gate->setPort(ID::B, RTLIL::SigSpec(hw));
            gate->set_bool_attribute(ID(gsm_icg_clk));
            g_icg2en_out = true;
            fprintf(stderr, "icg2en: exported gated clock %s held via %s\n",
                    g.wire->name.c_str(), hw->name.c_str());
        }
        g_icg2en_used = true;
    }
    // Deferred: a latch freed mid-loop would leave stale drv pointers for
    // later cones sharing it (the Q:=D connect above is additive and safe).
    for (RTLIL::Cell *l : latches_connected)
        mod->remove(l);
}

int main(int argc, char **argv)
{
    if (getenv("GSM_U32") && *getenv("GSM_U32") == '1')
        g_u32 = true;
    if (getenv("GSM_ACTIVITY_CENSUS"))
        g_census = true;
    if (getenv("GSM_CENSUS_DEPTH"))
        g_census_depth = atoi(getenv("GSM_CENSUS_DEPTH"));
    if (getenv("GSM_CENSUS_BLOCK"))
        g_census_block = atoi(getenv("GSM_CENSUS_BLOCK"));
    if (getenv("GSM_WIDE64"))
        g_w64 = true;
    if (getenv("GSM_COMB_SPLIT"))
        g_comb_split = atoi(getenv("GSM_COMB_SPLIT"));
    if (getenv("GSM_GATED")) {
        g_gated = true;
        if (getenv("GSM_GATED_BLOCK"))
            g_gated_block = atoi(getenv("GSM_GATED_BLOCK"));
        if (g_census) {
            fprintf(stderr, "GSM_GATED: census disabled (mutually exclusive)\n");
            g_census = false;
        }
    }

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
    // Errors ALWAYS reach stderr: a yosys log_error otherwise vanishes (no
    // console stream is connected in library mode) and the subtree silently
    // stays interpreted — VeeR's ifu_aln_ctl failed that way from day one
    // with nothing in any log. log_errfile is yosys's errors-only channel
    // (log_error_stderr alone only redirects EXISTING stdout log files).
    // Full diagnostics remain opt-in via GSM_LOG.
    Yosys::log_errfile = stderr;
    if (getenv("GSM_LOG"))              // surface all yosys diagnostics
        Yosys::log_streams.push_back(&std::cerr);

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
    // -check: a module with no definition would otherwise become a silent
    // blackbox (dangling-zero outputs) — measured on Vortex: VX_multiplier/
    // VX_uuid_gen missing from the file list stalled fetch with no error.
    Yosys::run_pass(std::string("hierarchy -check -top ") + top_name);
    Yosys::run_pass("proc");
    Yosys::run_pass("flatten");
    Yosys::run_pass("opt -keepdc");
    // Lower clock-enable and SYNCHRONOUS reset into the FF's D logic ($sdff/
    // $dffe -> plain $dff + an explicit mux). Async resets ($adff) are left for
    // sm_reset. Without this the codegen drops sync resets (q_next = d only).
    Yosys::run_pass("dffunmap");
    Yosys::run_pass("opt_clean");   // tidy WITHOUT opt_dff re-absorbing the reset into $dff SRST

    // GSM_ICG2EN: rewrite latch+AND internal gated clocks to clock enables.
    // Runs BEFORE the CXXRTL value plane and the SigMap/redirect build so
    // both engines and all downstream analysis see the same rewritten cells.
    if (getenv("GSM_ICG2EN"))
        icg2en_rewrite(Yosys::yosys_get_design()->top_module());

    // ---- VALUE-PLANE ENGINE: emit CXXRTL from the SAME RTLIL ---------------
    // Run here, immediately after the netlist the hand-written C emitter is
    // about to analyse, so both engines see bit-identical logic.  -O6 (input
    // ports become value<N>, direct write) and -g0 (debug_info is dead weight:
    // eval() machine code is byte-identical at every -g level, so -g0 costs
    // nothing at run time and is 12x smaller source / 5x faster to compile).
    const bool cxx_mode = getenv("GSM_CXXRTL") != NULL;
    std::string cxx_base = output_file;
    if (cxx_base.size() > 2 && cxx_base.compare(cxx_base.size() - 2, 2, ".c") == 0)
        cxx_base = cxx_base.substr(0, cxx_base.size() - 2);
    const std::string cxx_cc = cxx_base + "_cxx.cc";
    const std::string cxx_h  = cxx_base + "_cxx.h";
    if (cxx_mode) {
        const char *opt = getenv("GSM_CXXRTL_OPT");
        Yosys::run_pass("write_cxxrtl -header " + std::string(opt ? opt : "-O6 -g0")
                        + " " + cxx_cc);
        fprintf(stderr, "  value-plane: wrote %s\n", cxx_cc.c_str());
    }

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
    // One slice of a SLICED register: yosys opt_dff splits a wide per-byte-
    // enable register (VeeR byteen recirculation idiom) into several narrow
    // $dff-family cells whose Q chunks are disjoint slices of ONE wire
    // ($auto$ff.cc:NNN:slice$N). Reads resolve by wire cname + wire-relative
    // offset (sig_expr), so the state variable must span the FULL wire; each
    // slice cell's clocked commit becomes a masked read-modify-write of its
    // own bit range under that cell's OWN enable/reset/init semantics.
    struct RegSlice {
        std::string d_expr;      // narrow (<=64b) slice D as scalar C expr
        RTLIL::SigSpec d_sig;    // slice D bits (wide commit / late-cone seed)
        std::string en_expr;     // empty = always enabled
        RTLIL::SigSpec en_sig;
        std::string arst_expr;   // async-reset assert condition; "" = none
        RTLIL::SigSpec arst_sig;
        bool arst_pol = true;
        RTLIL::Const arst_const; // slice-width async-reset value (if has_arst)
        bool has_arst = false;
        RTLIL::Const init_const; // slice-width power-on init (if has_init)
        bool has_init = false;
        std::string src;         // source location
        int offset = 0;          // WIRE-relative bit offset of this slice
        int width  = 0;
    };
    struct RegInfo {
        std::string name;
        std::string d_expr;       // narrow (<=64b) D as a scalar C expr
        RTLIL::SigSpec d_sig;      // wide (>64b) D, committed via emit_materialize
        std::string en_expr;  // empty = always enabled
        RTLIL::SigSpec en_sig;  // raw EN port (for late D-cone analysis)
        std::string src;      // source location "file:line.col"
        int width;
        unsigned __int128 arst_val;
        unsigned __int128 init_val = 0;  // power-on value (Q wire `init` attr)
        bool has_init = false;           // distinct from arst_val (RESET value)
        std::string arst_expr; // async-reset ($adff) assert condition; "" = none
        RTLIL::SigSpec arst_sig;  // raw ARST port (for cone inlining)
        bool arst_pol = true;
        std::string clk_name; // CLK net cname (redirect-folded); "_clk" = main clk
        int clk_group = 0;    // 0 = main clk; 1+i = extra_clocks[i]
        std::string raw_qname; // raw Q-wire name (no backslash) for the scan-bench
                               // manifest so json2bench names the PPI to match
                               // this reg's cname()d state_t field
        std::vector<RegSlice> slices; // non-empty => MERGED sliced register:
                               // state spans the whole Q wire; d_expr/d_sig/
                               // en/arst at this level are unused, each slice
                               // carries its own
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
    // Q bit -> (reg alias name, bit offset) — for inlining async-reset cones
    dict<RTLIL::SigBit, std::pair<std::string,int>> g_regbit;

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
            // $meminit_v2 packs WORDS consecutive words into one DATA
            // constant (word w = bits [w*width, (w+1)*width)).  Stuffing the
            // whole constant into init[addr] scrambled every multi-word ROM:
            // yosys proc_rom lowers case tables this way, and the Vortex
            // popcount ROM read back its full table as word 0 (mshr dealloc
            // count stuck at 4 -> the fetch-fill path never issued).
            int words = cell->getParam(ID(WORDS)).as_int();
            if (words < 1) words = 1;
            for (int w = 0; w < words; w++) {
                unsigned __int128 data = 0;
                for (int i = width - 1; i >= 0; i--) {
                    int bit = w * width + i;
                    data = (data << 1)
                         | (bit < data_const.size()
                            && data_const[bit] == RTLIL::S1 ? 1 : 0);
                }
                mem.init[addr + w] = data;
            }
            if ((int)addr + words > mem.depth)
                mem.depth = addr + words;
        }
    }

    // Second pass: get depth from $memrd AND $memwr port ABITS. Depth must
    // be the MAX over every source: a $meminit that touches only word 0
    // used to leave depth=1, and the old `if (depth == 0)` guard then kept
    // it there — the array was allocated one word deep and every write to a
    // higher address silently corrupted the NEXT state_t member. (Found as
    // the Vortex multi-lane-load hang: the dcache MSHR's 16-entry
    // addr_table got depth 1; entry-1 writes clobbered mshr_store slot 0's
    // tag, so the second same-line miss replayed a garbage tag and the
    // coalescer's slot-1 response never matched.)
    for (auto &c : mod->cells_) {
        auto *cell = c.second;
        auto type = cell->type.str();
        if (type == "$memrd" || type == "$memrd_v2" ||
            type == "$memwr" || type == "$memwr_v2") {
            std::string memid = cell->getParam(ID(MEMID)).decode_string();
            auto &mem = memories[memid];
            int abits = cell->getParam(ID(ABITS)).as_int();
            if ((1 << abits) > mem.depth)
                mem.depth = 1 << abits;
            mem.width = cell->getParam(ID(WIDTH)).as_int();
            mem.abits = abits;
            mem.name = cname(memid);
        }
    }

    std::set<std::string> reg_names_used;

    // Sliced-register support: count how many $dff-family cells drive (part
    // of) each raw Q wire. A wire hosting >1 FF cell — or one cell whose Q
    // chunk is a partial slice of its wire — takes the merged-register path
    // below instead of the whole-wire assumption.
    std::map<RTLIL::Wire*, int> qwire_ncells;
    for (auto &c : mod->cells_) {
        auto *cell = c.second;
        auto type = cell->type.str();
        bool is_reg = (type == "$adff" || type == "$dff" || type == "$adffe"
                       || type == "$dffe" || type == "$sdff" || type == "$sdffe");
        if (!is_reg || !cell->hasPort(ID::Q)) continue;
        std::set<RTLIL::Wire*> seen;
        for (auto &qc : cell->getPort(ID::Q).chunks())
            if (qc.wire && seen.insert(qc.wire).second)
                qwire_ncells[qc.wire]++;
    }
    std::map<RTLIL::Wire*, size_t> sliced_reg_idx;  // Q wire -> merged reg index

    // Main pass: classify cells
    for (auto &c : mod->cells_) {
        auto *cell = c.second;
        auto type = cell->type.str();

        if (type == "$scopeinfo") continue;
        if (type == "$meminit" || type == "$meminit_v2") continue;  // already handled

        bool is_reg = (type == "$adff" || type == "$dff" || type == "$adffe"
                       || type == "$dffe" || type == "$sdff" || type == "$sdffe");
        if (is_reg) {
            auto &q = cell->getPort(ID::Q);
            auto &d = cell->getPort(ID::D);
            // Whole-wire check: the classic path below assumes this cell's Q
            // spans an ENTIRE wire at offset 0 and no other FF cell shares it.
            // Anything else (opt_dff slice cells, partial-Q, multi-chunk Q)
            // takes the merged sliced-register path.
            std::vector<RTLIL::SigChunk> qch(q.chunks().begin(), q.chunks().end());
            bool whole_wire = qch.size() == 1 && qch[0].wire != nullptr
                && qch[0].offset == 0 && qch[0].width == qch[0].wire->width
                && qwire_ncells[qch[0].wire] == 1;
            if (!whole_wire) {
                // --- sliced / partial-Q register path ----------------------
                // Emit ONE full-wire-width state variable named for the wire;
                // record each Q chunk as a RegSlice (offset, width, its own
                // D/EN/ARST/init). The commit becomes a masked RMW at the
                // slice's offset. (The old code named the state var after the
                // full wire but committed only the first-seen slice's narrow
                // D at offset 0, and routed sibling slices through the name-
                // collision fallback into cell-named vars that were written
                // every cycle but read zero times — the register collapsed to
                // one misplaced slice, all other bits constant 0.)
                RTLIL::SigSpec qs = sigmap(q);
                std::string cell_clk = "_clk";
                if (cell->hasPort(ID::CLK)) {
                    RTLIL::SigBit cb = sigmap(cell->getPort(ID::CLK))[0];
                    auto rit = redirect.find(cb);
                    if (rit != redirect.end()) cb = rit->second;
                    if (cb.wire) cell_clk = cname(cb.wire->name.str());
                }
                int pos = 0;
                for (auto &qc : qch) {
                    if (qc.wire == nullptr) {
                        fprintf(stderr, "gen_statemachine: %s cell %s has a "
                                "non-wire Q chunk — declining\n",
                                type.c_str(), cell->name.c_str());
                        exit(1);
                    }
                    size_t ri;
                    auto mit = sliced_reg_idx.find(qc.wire);
                    if (mit == sliced_reg_idx.end()) {
                        std::string raw_q = qc.wire->name.str();
                        std::string wname = cname(raw_q);
                        if (reg_names_used.count(wname)) {
                            // A slice must NEVER take the cell-name collision
                            // fallback: reads resolve by wire cname, so the
                            // fallback var would be written but never read.
                            fprintf(stderr, "gen_statemachine: sliced register "
                                    "wire %s collides with an existing register "
                                    "name — declining\n", wname.c_str());
                            exit(1);
                        }
                        reg_names_used.insert(wname);
                        RegInfo nreg;
                        nreg.name = wname;
                        nreg.raw_qname = (!raw_q.empty() && raw_q[0] == '\\')
                                       ? raw_q.substr(1) : raw_q;
                        nreg.width = qc.wire->width;
                        nreg.clk_name = cell_clk;
                        registers.push_back(nreg);
                        ri = registers.size() - 1;
                        sliced_reg_idx[qc.wire] = ri;
                    } else ri = mit->second;
                    RegInfo &mreg = registers[ri];
                    if (mreg.clk_name != cell_clk) {
                        fprintf(stderr, "gen_statemachine: slices of register "
                                "%s use different clocks (%s vs %s) — "
                                "declining\n", mreg.name.c_str(),
                                mreg.clk_name.c_str(), cell_clk.c_str());
                        exit(1);
                    }
                    for (auto &osl : mreg.slices)
                        if (qc.offset < osl.offset + osl.width
                            && osl.offset < qc.offset + qc.width) {
                            fprintf(stderr, "gen_statemachine: overlapping Q "
                                    "slices on register %s — declining\n",
                                    mreg.name.c_str());
                            exit(1);
                        }
                    RegSlice sl;
                    sl.offset = qc.offset;
                    sl.width  = qc.width;
                    sl.d_sig  = d.extract(pos, qc.width);
                    sl.d_expr = is_wide(sl.width) ? "" : sig_expr(sl.d_sig, sigmap);
                    sl.src    = cell->get_src_attribute();
                    // Q bit -> (merged reg name, WIRE-relative offset)
                    for (int qi = 0; qi < qc.width; qi++)
                        if (qs[pos + qi].wire)
                            g_regbit[qs[pos + qi]] = { mreg.name, qc.offset + qi };
                    // power-on init: Q wire `init` attr bits for THIS slice
                    {
                        std::vector<RTLIL::State> iv(qc.width, RTLIL::Sx);
                        bool any = false;
                        for (int qi = 0; qi < qc.width; qi++) {
                            RTLIL::SigBit b = qs[pos + qi];
                            if (b.wire && b.wire->attributes.count(ID::init)) {
                                const RTLIL::Const &wi = b.wire->attributes.at(ID::init);
                                if (b.offset < wi.size() &&
                                    (wi[b.offset] == RTLIL::S0 ||
                                     wi[b.offset] == RTLIL::S1)) {
                                    iv[qi] = wi[b.offset]; any = true;
                                }
                            }
                        }
                        if (any) { sl.has_init = true; sl.init_const = RTLIL::Const(iv); }
                    }
                    if (type == "$adff" || type == "$adffe") {
                        auto av = cell->getParam(ID(ARST_VALUE));
                        std::vector<RTLIL::State> rv(qc.width, RTLIL::S0);
                        for (int qi = 0; qi < qc.width; qi++)
                            if (pos + qi < av.size() && av[pos + qi] == RTLIL::S1)
                                rv[qi] = RTLIL::S1;
                        sl.arst_const = RTLIL::Const(rv);
                        sl.has_arst = true;
                        if (cell->hasPort(ID::ARST)) {
                            std::string a = sig_expr(cell->getPort(ID::ARST), sigmap);
                            bool pol = !cell->hasParam(ID(ARST_POLARITY))
                                     || cell->getParam(ID(ARST_POLARITY)).as_bool();
                            sl.arst_expr = pol ? a : ("(!(" + a + "))");
                            sl.arst_sig = cell->getPort(ID::ARST);
                            sl.arst_pol = pol;
                        }
                    }
                    if (type == "$dffe" || type == "$adffe" || type == "$sdffe") {
                        if (cell->hasPort(ID::EN)) {
                            sl.en_expr = sig_expr(cell->getPort(ID::EN), sigmap);
                            sl.en_sig  = cell->getPort(ID::EN);
                            if (cell->hasParam(ID(EN_POLARITY)) &&
                                !cell->getParam(ID(EN_POLARITY)).as_bool())
                                sl.en_expr = "(!" + sl.en_expr + ")";
                        }
                    }
                    mreg.slices.push_back(sl);
                    pos += qc.width;
                }
                continue;
            }
            RegInfo reg;
            // Use wire name, but fall back to cell name on collision
            std::string raw_q = (*q.chunks().begin()).wire->name.str();
            std::string wname = cname(raw_q);
            if (reg_names_used.count(wname)) {
                wname = cname(cell->name.str());
                raw_q = cell->name.str();
            }
            reg_names_used.insert(wname);
            reg.name = wname;
            // strip yosys public '\' so it matches write_json's netname keys
            reg.raw_qname = (!raw_q.empty() && raw_q[0] == '\\')
                          ? raw_q.substr(1) : raw_q;
            reg.width = q.size();
            {   // Q bit -> (reg alias name, offset), for arst-cone inlining
                RTLIL::SigSpec qs = sigmap(q);
                for (int qi = 0; qi < qs.size(); qi++)
                    if (qs[qi].wire) g_regbit[qs[qi]] = { wname, qi };
            }
            reg.d_sig = d;
            // Wide D is committed via emit_materialize (sig_expr can't return a
            // limb array); only render the scalar string for narrow regs.
            reg.d_expr = is_wide(reg.width) ? "" : sig_expr(d, sigmap);
            reg.src = cell->get_src_attribute();

            // Power-on init: the Q wire's `init` attribute (from a Verilog reg
            // initializer, i.e. the VHDL signal default). This is DISTINCT from
            // the async RESET value (arst_val) -- e.g. `signal r := 0` with an
            // async reset to DEADBEEF powers on at 0. sm_reset must use this,
            // not arst_val, or the register starts at its reset value.
            {
                RTLIL::SigSpec qs = sigmap(q);
                unsigned __int128 iv = 0; bool any = false;
                for (int qi = 0; qi < qs.size() && qi < 128; qi++) {
                    RTLIL::SigBit b = qs[qi];
                    if (b.wire && b.wire->attributes.count(ID::init)) {
                        const RTLIL::Const &wi = b.wire->attributes.at(ID::init);
                        if (b.offset < wi.size() && wi[b.offset] == RTLIL::S1) {
                            iv |= ((unsigned __int128)1 << qi); any = true;
                        }
                        else if (b.offset < wi.size() && wi[b.offset] == RTLIL::S0)
                            any = true;
                    }
                }
                if (any) { reg.has_init = true; reg.init_val = iv; }
            }

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
                    reg.arst_sig = cell->getPort(ID::ARST);
                    reg.arst_pol = pol;
                }
            }

            // Get clock enable if present
            if (type == "$dffe" || type == "$adffe" || type == "$sdffe") {
                if (cell->hasPort(ID::EN)) {
                    reg.en_expr = sig_expr(cell->getPort(ID::EN), sigmap);
                    reg.en_sig  = cell->getPort(ID::EN);
                    // Check enable polarity
                    if (cell->hasParam(ID(EN_POLARITY)) &&
                        !cell->getParam(ID(EN_POLARITY)).as_bool())
                        reg.en_expr = "(!" + reg.en_expr + ")";
                }
            }

            // Clock net (fold through redirect exactly like data reads, so an
            // internal gated clock that `connect`s to \clk -> cname "_clk" -> main
            // group). The bridge advances each flop on ITS clock's posedge.
            // A VECTOR clock wire (EH2's per-thread active_thread_l2clk[1:0])
            // must yield one group per BIT: keying on the wire name alone
            // merged both threads' flops into one group edge-detected on
            // bit 0, so thread-1 state advanced on thread-0's clock (interp
            // holds it -- its gated clock never rises) and per-thread state
            // (lsu_store_stall_any...) diverged.  Encode the bit as a
            // "__b<N>" suffix; the bridge parses it and extracts that bit
            // for the group's edge detect.
            reg.clk_name = "_clk";
            if (cell->hasPort(ID::CLK)) {
                RTLIL::SigBit cb = sigmap(cell->getPort(ID::CLK))[0];
                auto rit = redirect.find(cb);
                if (rit != redirect.end()) cb = rit->second;
                if (cb.wire) {
                    reg.clk_name = cname(cb.wire->name.str());
                    if (cb.wire->width > 1)
                        reg.clk_name += "__b" + std::to_string(cb.offset);
                }
            }

            registers.push_back(reg);
        } else {
            comb_cells.push_back(cell);
        }
    }

    // --- COMB-ONLY CHUNKS ARE DECLINED, LOUDLY -------------------------------
    // A chunk with ZERO registers (and no memories) has no state to advance: it
    // is a pure function the interpreter already evaluates in one delta.  The
    // bridge cannot make it faster -- it can only add a call and a DELTA HOP on
    // every input change -- and when the wire it carries is a CLOCK that hop is
    // a correctness disaster.
    //
    // MEASURED, full VeeR-EH2 2026-07-31: rvoclkhdr__63cc is literally
    // `assign l1clk = clk;` (the FPGA build's clock gate collapses to a wire).
    // Installed 7x as an accel chunk, it put a bridge in the clock path of its
    // whole downstream cone; NVC_ACCEL_VERIFY's single divergence across the
    // entire run was that chunk's L1CLK reading 0 at the first clk rising edge
    // (5ns+48, interp=1 accel=0).  In the driving run every flop behind the
    // gated clock therefore never clocked -- wrong from the first retirement.
    // The comb-chain c1c output-drop defect is the same family; declining
    // comb-only chunks quarantines both.
    //
    // Zero benefit, real hazard, and a decline structurally cannot create a
    // wrong answer.  GSM_ALLOW_COMB=1 overrides for experiments.
    if (registers.empty() && memories.empty() && !getenv("GSM_ALLOW_COMB")) {
        fprintf(stderr, "gen_statemachine: declining '%s': comb-only "
                "(0 registers, 0 memories) -- nothing to accelerate, and a "
                "bridged pure-comb path adds a delta hop (clock-path hazard; "
                "see rvoclkhdr/L1CLK)\n", mod->name.c_str());
        return 1;
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
                // Strip a per-bit "__b<N>" suffix before the port test: the
                // PORT is the vector wire; the suffix only selects the bit.
                std::string port_name = reg.clk_name;
                size_t bpos = port_name.rfind("__b");
                if (bpos != std::string::npos &&
                    port_name.find_first_not_of("0123456789", bpos + 3)
                        == std::string::npos)
                    port_name = port_name.substr(0, bpos);
                if (!input_cnames.count(port_name)) {
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

    // GSM_FSM_SPEC: pick ONE primary FSM to per-state specialize — the smallest
    // reachable state space (fewest emitted copies), capped at 64 states. Only
    // the single-clock emission path specializes (checked at the emit site).
    if (getenv("GSM_FSM_SPEC") && !fsms.empty()) {
        FsmInfo *best = nullptr;
        for (auto &f : fsms)
            if (f.max_states <= 64 && (!best || f.max_states < best->max_states))
                best = &f;
        if (best) {
            g_spec_regname = best->name;
            g_spec_nstates = best->max_states;
            fprintf(stderr, "FSM-SPEC: per-state specializing on %s (%d states)\n",
                    best->name.c_str(), best->max_states);
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
    // BIT-PRECISE: record which BIT RANGE each cell drives, not just the wire.
    // A wire that is partially written (e.g. VeeR's dec_i0_brp input, whose bit 46
    // is overwritten internally while bit 51 is read as the branch-error input)
    // otherwise makes a reader of an UNwritten bit falsely depend on the cell that
    // writes a DIFFERENT bit — a false cycle (scatter -> br_error -> scatter's own
    // select) that the DFS breaks arbitrarily, emitting the scatter before its
    // select and reading a stale (0) select. Keyed by the RAW driven wire+offset.
    struct DrvRange { int off, width; RTLIL::Cell *cell; };
    std::map<RTLIL::Wire*, std::vector<DrvRange>> wire_driver;
    for (auto *cell : comb_cells) {
        // Check Y port (most cells) and DATA port ($memrd). Key by the RAW driven
        // wire (the name the cell writes); readers reach it through the redirect.
        for (auto port_id : {ID::Y, ID(DATA)}) {
            if (cell->hasPort(port_id)) {
                for (auto &chunk : cell->getPort(port_id).chunks())
                    if (chunk.wire)
                        wire_driver[chunk.wire].push_back({chunk.offset, chunk.width, cell});
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
                RTLIL::SigBit rb = (rit != redirect.end() ? rit->second : bit);
                if (rb.wire == nullptr) continue;
                auto it = wire_driver.find(rb.wire);
                if (it == wire_driver.end()) continue;
                // Depend ONLY on cells that drive THIS bit (bit-precise), so a read
                // of an unwritten bit of a partially-driven wire doesn't chain to the
                // driver of some other bit (the false-cycle -> stale-select bug).
                for (auto &dr : it->second)
                    if (rb.offset >= dr.off && rb.offset < dr.off + dr.width)
                        topo_visit(dr.cell);
            }
        }
        sorted.push_back(cell);
    };
    for (auto *cell : comb_cells)
        topo_visit(cell);

    // ---- GSM_MASKED_COMB analysis (phase 1): member-partition census ----
    // The fused-chunk comb settle is activity-BLIND (measured: act_lo vs
    // act_hi accel instruction counts 0.8% apart while interp scales 2.8x
    // with activity) because every clock pass re-evaluates the whole
    // network.  The planned fix partitions comb cells by fused-wrapper
    // member (flattened name prefix "_u<K>_") and re-runs only members
    // whose inputs or state changed, with cross-member comb nets persisted
    // in state_t.  This block is the DESIGN-SIZING census: it computes the
    // partition, the member-affects DAG (cross-member comb nets + register
    // reads) and its transitive closure, and reports the numbers that
    // decide the emission design (persisted-net count = state bloat;
    // closure density = worst-case re-run set).  Analysis only: emission
    // is unchanged.
    if (getenv("GSM_MASKED_COMB") != NULL) {
        auto member_of_name = [&](const std::string &n) -> int {
            size_t p = 0;
            if (p < n.size() && n[p] == '_') p++;
            if (p >= n.size() || n[p] != 'u') return -1;
            size_t q = p + 1, s0 = q;
            while (q < n.size() && isdigit((unsigned char)n[q])) q++;
            if (q == s0 || q >= n.size() || n[q] != '_') return -1;
            return atoi(n.substr(s0, q - s0).c_str());
        };
        auto member_of_cell = [&](RTLIL::Cell *cell) -> int {
            for (auto port_id : {ID::Y, ID(DATA)})
                if (cell->hasPort(port_id))
                    for (auto &chunk : cell->getPort(port_id).chunks())
                        if (chunk.wire)
                            return member_of_name(cname(chunk.wire->name.str()));
            return member_of_name(cname(cell->name.str()));
        };
        int nmem = 0;
        std::map<RTLIL::Cell*, int> cmember;
        int orphan_cells = 0;
        for (auto *cell : sorted) {
            int mb = member_of_cell(cell);
            cmember[cell] = mb;
            if (mb < 0) orphan_cells++;
            if (mb + 1 > nmem) nmem = mb + 1;
        }
        std::map<std::string, int> name_member;   // register name -> member
        for (auto &reg : registers)
            name_member[reg.name] = member_of_name(reg.name);
        std::map<RTLIL::Wire*, int> wprod;        // comb wire -> producer member
        for (auto *cell : sorted)
            for (auto port_id : {ID::Y, ID(DATA)})
                if (cell->hasPort(port_id))
                    for (auto &chunk : cell->getPort(port_id).chunks())
                        if (chunk.wire)
                            wprod[chunk.wire] = cmember[cell];
        std::set<RTLIL::Wire*> xnets;             // cross-member comb nets
        long xbits = 0;
        std::vector<std::set<int>> affects(nmem > 0 ? nmem : 1);
        long regedges = 0;
        for (auto *cell : sorted) {
            const int cm = cmember[cell];
            for (auto &conn : cell->connections()) {
                if (cell->output(conn.first)) continue;
                RTLIL::SigSpec ms = sigmap(conn.second);
                for (auto &bit : ms) {
                    auto rit = redirect.find(bit);
                    RTLIL::SigBit rb = (rit != redirect.end() ? rit->second : bit);
                    if (rb.wire == nullptr) continue;
                    auto wp = wprod.find(rb.wire);
                    if (wp != wprod.end() && wp->second >= 0 && cm >= 0
                        && wp->second != cm) {
                        if (xnets.insert(rb.wire).second)
                            xbits += rb.wire->width;
                        affects[wp->second].insert(cm);
                        continue;
                    }
                    // cross-member REGISTER read: state-persisted by nature,
                    // but member B's commit must re-run reader A's comb.
                    auto nm = name_member.find(cname(rb.wire->name.str()));
                    if (nm != name_member.end() && nm->second >= 0 && cm >= 0
                        && nm->second != cm
                        && affects[nm->second].insert(cm).second)
                        regedges++;
                }
            }
        }
        // transitive closure (affects*), incl self
        std::vector<std::set<int>> clo(nmem > 0 ? nmem : 1);
        for (int k = 0; k < nmem; k++) {
            std::vector<int> stk(1, k);
            while (!stk.empty()) {
                int v = stk.back(); stk.pop_back();
                if (!clo[k].insert(v).second) continue;
                for (int w2 : affects[v]) stk.push_back(w2);
            }
        }
        size_t cmax = 0, csum = 0;
        for (int k = 0; k < nmem; k++) {
            cmax = std::max(cmax, clo[k].size());
            csum += clo[k].size();
        }
        fprintf(stderr, "gen_statemachine: MASKED_COMB census: members=%d "
                "cells=%zu orphan_cells=%d xnets=%zu xbits=%ld regedges=%ld "
                "closure avg=%.1f max=%zu\n",
                nmem, sorted.size(), orphan_cells, xnets.size(), xbits,
                regedges, nmem > 0 ? (double)csum / nmem : 0.0, cmax);
    }

    // ---- async-reset cone inliner -------------------------------------
    // The "Async reset overrides" block runs BEFORE the comb wires are
    // declared/evaluated, so a DERIVED reset (arst = comb of inputs, e.g.
    // VeeR dbg's `or(rst_l, dbg_dm_rst_l) & dbg_rst_l`) referenced a C net
    // that does not exist yet -> compile error -> the whole chunk was left
    // interpreted. Render such conditions by recursively inlining the cone
    // from input/register aliases (reset cones are a handful of gates).
    // Unsupported cell types make the render fail -> status-quo fallback.
    dict<RTLIL::SigBit, RTLIL::Cell*> g_bitdrv;
    for (auto *cell : sorted)
        for (auto &conn : cell->connections())
            if (cell->output(conn.first)) {
                RTLIL::SigSpec os = sigmap(conn.second);
                for (auto &b : os)
                    if (b.wire) g_bitdrv[b] = cell;
            }
    std::function<bool(RTLIL::SigBit, std::string &, int &)> bit_expr =
        [&](RTLIL::SigBit b, std::string &out_s, int &budget) -> bool {
        if (--budget < 0) return false;
        b = sigmap(b);
        if (b.wire == nullptr) {           // constant
            out_s = (b.data == RTLIL::S1) ? "1" : "0";
            return true;
        }
        auto rq = g_regbit.find(b);
        if (rq != g_regbit.end()) {        // register Q bit: use the alias
            char buf[160];
            const auto &nm = rq->second.first; int off = rq->second.second;
            // wide regs alias as uint32_t limb arrays, narrow as scalars
            bool wide = false;
            for (auto &r : registers)
                if (r.name == nm) { wide = is_wide(r.width); break; }
            if (wide)
                snprintf(buf, sizeof buf, "((%s[%d] >> %d) & 1)",
                         nm.c_str(), off >> 5, off & 31);
            else
                snprintf(buf, sizeof buf, "((%s >> %d) & 1)", nm.c_str(), off);
            out_s = buf;
            return true;
        }
        if (b.wire->port_input) {          // input alias
            std::string wn = cname(b.wire->name.str());
            char buf[160];
            if (wn == "_clk" || wn == "_rst") return false;
            if (is_wide(b.wire->width))
                snprintf(buf, sizeof buf, "((%s[%d] >> %d) & 1)",
                         wn.c_str(), b.offset >> 5, b.offset & 31);
            else
                snprintf(buf, sizeof buf, "((%s >> %d) & 1)",
                         wn.c_str(), b.offset);
            out_s = buf;
            return true;
        }
        auto dit = g_bitdrv.find(b);
        if (dit == g_bitdrv.end()) return false;
        RTLIL::Cell *c = dit->second;
        std::string ty = c->type.str();
        auto bin = [&](const char *op) -> bool {
            RTLIL::SigSpec A = sigmap(c->getPort(ID::A));
            RTLIL::SigSpec B = sigmap(c->getPort(ID::B));
            // bitwise ops: operate on the SAME bit lane as the output bit
            RTLIL::SigSpec Y = sigmap(c->getPort(ID::Y));
            int lane = -1;
            for (int i = 0; i < Y.size(); i++) if (Y[i] == b) { lane = i; break; }
            if (lane < 0 || lane >= A.size() || lane >= B.size()) return false;
            std::string sa, sb2;
            if (!bit_expr(A[lane], sa, budget) || !bit_expr(B[lane], sb2, budget))
                return false;
            out_s = "(" + sa + " " + op + " " + sb2 + ")";
            return true;
        };
        auto reduce = [&](const char *op, bool invert) -> bool {
            RTLIL::SigSpec A = sigmap(c->getPort(ID::A));
            if (A.size() > 8) return false;
            std::string acc;
            for (int i = 0; i < A.size(); i++) {
                std::string sa;
                if (!bit_expr(A[i], sa, budget)) return false;
                acc = acc.empty() ? sa : "(" + acc + " " + op + " " + sa + ")";
            }
            out_s = invert ? "(!" + acc + ")" : acc;
            return true;
        };
        if (ty == "$and")  return bin("&");
        if (ty == "$or")   return bin("|");
        if (ty == "$xor")  return bin("^");
        if (ty == "$not" || ty == "$logic_not" || ty == "$pos" || ty == "$buf") {
            RTLIL::SigSpec A = sigmap(c->getPort(ID::A));
            if (ty == "$logic_not" && A.size() != 1) return false;
            RTLIL::SigSpec Y = sigmap(c->getPort(ID::Y));
            int lane = -1;
            for (int i = 0; i < Y.size(); i++) if (Y[i] == b) { lane = i; break; }
            if (lane < 0 || lane >= A.size()) return false;
            std::string sa;
            if (!bit_expr(A[lane], sa, budget)) return false;
            out_s = (ty == "$pos" || ty == "$buf") ? sa : "(!" + sa + ")";
            return true;
        }
        if (ty == "$reduce_or"  || ty == "$reduce_bool") return reduce("|", false);
        if (ty == "$reduce_and") return reduce("&", false);
        if (ty == "$logic_and") {
            std::string sa, sb2;
            RTLIL::SigSpec A = sigmap(c->getPort(ID::A)), B = sigmap(c->getPort(ID::B));
            if (A.size() != 1 || B.size() != 1) return false;
            if (!bit_expr(A[0], sa, budget) || !bit_expr(B[0], sb2, budget)) return false;
            out_s = "(" + sa + " && " + sb2 + ")";
            return true;
        }
        if (ty == "$logic_or") {
            std::string sa, sb2;
            RTLIL::SigSpec A = sigmap(c->getPort(ID::A)), B = sigmap(c->getPort(ID::B));
            if (A.size() != 1 || B.size() != 1) return false;
            if (!bit_expr(A[0], sa, budget) || !bit_expr(B[0], sb2, budget)) return false;
            out_s = "(" + sa + " || " + sb2 + ")";
            return true;
        }
        if (ty == "$mux") {
            RTLIL::SigSpec A = sigmap(c->getPort(ID::A)), B = sigmap(c->getPort(ID::B));
            RTLIL::SigSpec S = sigmap(c->getPort(ID(S))), Y = sigmap(c->getPort(ID::Y));
            int lane = -1;
            for (int i = 0; i < Y.size(); i++) if (Y[i] == b) { lane = i; break; }
            if (lane < 0 || S.size() != 1 || lane >= A.size() || lane >= B.size())
                return false;
            std::string ss, sa, sb2;
            if (!bit_expr(S[0], ss, budget) || !bit_expr(A[lane], sa, budget)
                || !bit_expr(B[lane], sb2, budget)) return false;
            out_s = "(" + ss + " ? " + sb2 + " : " + sa + ")";
            return true;
        }
        return false;
    };
    // Pre-render each register's (and slice's) inlined reset condition
    // (unchanged expr = fallback)
    auto inline_arst = [&](std::string &arst_expr, const RTLIL::SigSpec &arst_sig,
                           bool arst_pol) {
        if (arst_expr.empty() || arst_sig.size() == 0) return;
        RTLIL::SigSpec as = sigmap(arst_sig);
        if (as.size() != 1) return;
        RTLIL::SigBit ab = as[0];
        // direct input/reg/const references are already fine — only DERIVED
        // (comb-driven) reset nets need inlining
        if (ab.wire == nullptr || ab.wire->port_input || g_regbit.count(ab))
            return;
        std::string s; int budget = 96;
        if (bit_expr(ab, s, budget))
            arst_expr = arst_pol ? s : ("(!(" + s + "))");
        // else: leave as-is (status quo: chunk declines at compile)
    };
    for (auto &reg : registers) {
        inline_arst(reg.arst_expr, reg.arst_sig, reg.arst_pol);
        for (auto &sl : reg.slices)
            inline_arst(sl.arst_expr, sl.arst_sig, sl.arst_pol);
    }

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

    // Generate C code.  In value-plane mode the hand-written C emitter still
    // runs, UNCHANGED, but writes to "<base>.ref.c": it stays the ABI reference
    // and the fallback engine, and `output_file` (the model nvc's bridge
    // #includes) is written by the CXXRTL adapter emitter at the end of main.
    const std::string model_file = cxx_mode ? (std::string(output_file) + ".ref.c")
                                            : std::string(output_file);
    FILE *out = fopen(model_file.c_str(), "w");
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
    (void)any_wide; fprintf(out, "%s", g_w64 ? WIDE_RT64 : WIDE_RT);  // always: wide-mux path uses limb helpers even for <=64b
    // Dead-output pruning mask: bit i = output i (sm_output_order[]) is live.
    // The bridge clears bits for outputs whose consumer nexus has no readers;
    // cone cells exclusive to dead outputs are skipped at run time.
    // SIZED to the real output count (min 4 words for legacy layout): a
    // fixed [4] capped every model at 256 outputs, and pin-completion
    // wrappers legitimately exceed that.  sm_live_outputs_words lets the
    // bridge negotiate the copy size (absent symbol = legacy 4).
    int n_out_ports = 0;
    for (auto &w : mod->wires_)
        if (w.second->port_output) n_out_ports++;
    const int lo_words = std::max(4, (n_out_ports + 63) / 64);
    if (g_census) {
        if (g_census_block > 0) {
            // topo-block grouping: gid = position in `sorted` / K
            int idx = 0;
            for (auto *cell : sorted) {
                int blk = idx++ / g_census_block;
                while ((int)g_cns_groups.size() <= blk) {
                    char bn[16]; snprintf(bn, sizeof bn, "blk%04d",
                                          (int)g_cns_groups.size());
                    g_cns_gid[bn] = (int)g_cns_groups.size();
                    g_cns_groups.push_back(bn);
                }
                g_cns_gid_override[cell->name.str()] = blk;
            }
        }
        for (auto &c : mod->cells_) (void)cns_gid_of(c.second);
    }
    fprintf(out, "const int sm_live_outputs_words = %d;\n", lo_words);
    fprintf(out, "uint64_t sm_live_outputs[%d] = {", lo_words);
    for (int lw = 0; lw < lo_words; lw++)
        fprintf(out, "%s~0ull", lw ? "," : "");
    fprintf(out, "};\n\n");
    if (g_census) {
        fprintf(out, "enum { _CNS_NG = %d };\n"
                "static uint64_t _cns_shadow[_CNS_NG];\n"
                "static uint64_t _cns_runs[_CNS_NG];\n"
                "static uint64_t _cns_calls;\n"
                "static const char *_cns_names[_CNS_NG] = {",
                (int)g_cns_groups.size());
        for (auto &g : g_cns_groups)
            fprintf(out, "\"%s\",", g.c_str());
        fprintf(out, "};\n"
            "__attribute__((destructor)) static void _cns_report(void) {\n"
            "    if (_cns_calls == 0) return;\n"
            "    fprintf(stderr, \"ACTIVITY CENSUS (%%llu comb calls):\\n\",\n"
            "            (unsigned long long)_cns_calls);\n"
            "    for (int g = 0; g < _CNS_NG; g++)\n"
            "        fprintf(stderr, \"  %%-24s %%6.2f%%%% (%%llu)\\n\", _cns_names[g],\n"
            "                100.0*_cns_runs[g]/_cns_calls,\n"
            "                (unsigned long long)_cns_runs[g]);\n"
            "}\n\n");
    }
    if (g_icg2en_out)
        // Real base-clock value, poked by the bridge before each eval (the
        // comb-local _clk is pinned 0; inputs_t excludes the primary clock).
        // Read ONLY by icg2en-exported gated-clock gates.
        fprintf(out, "uint64_t sm_icg_clkval = 0;\n\n");

    // ---- GSM_GATED plan + file-scope persistence -------------------------
    // v1 scope: single clock group, no FSM specialization (checked when the
    // gated body is emitted; the plan itself is unconditional under g_gated).
    if (g_gated && !g_spec_regname.empty()) {
        fprintf(stderr, "GSM_GATED: disabled (FSM specialization active)\n");
        g_gated = false;
    }
    // GSM_ACTPROF=<file>: per-cell change counts (census depth-0 run.err
    // format: "  <name>  <pct>%% (<count>)").  When present, re-sort the
    // gated emission order with Kahn's algorithm, preferring high-activity
    // cells first — co-active cells cluster into the same blocks instead of
    // each hot cell dragging K-1 idle topo neighbours.  Any topological
    // order is valid for emission, so this only changes block quality.
    if (g_gated && getenv("GSM_ACTPROF")) {
        std::map<std::string, long> prof;
        FILE *pf = fopen(getenv("GSM_ACTPROF"), "r");
        if (pf) {
            char ln[1024], nb[512]; double pct; long cnt;
            while (fgets(ln, sizeof ln, pf))
                if (sscanf(ln, " %511s %lf%% (%ld)", nb, &pct, &cnt) == 3)
                    prof[nb] = cnt;
            fclose(pf);
        }
        fprintf(stderr, "GSM_ACTPROF: %zu cells profiled\n", prof.size());
        if (!prof.empty()) {
            // dependency edges among the comb cells via their nets
            std::map<std::string, RTLIL::Cell*> netw;   // net -> writer cell
            std::map<RTLIL::Cell*, std::vector<RTLIL::Cell*>> succ;
            std::map<RTLIL::Cell*, int> pred, opos;
            std::map<RTLIL::Cell*, long> act;
            int oi = 0;
            for (auto *cell : sorted) {
                opos[cell] = oi++;
                pred[cell] = 0;
                auto it = prof.find(cns_sanitize(cell->name.str()));
                act[cell] = it == prof.end() ? 0 : it->second;
            }
            for (auto *cell : sorted) {
                bool ismem = cell->type.str().compare(0, 6, "$memrd") == 0;
                for (auto &conn : cell->connections()) {
                    bool isout = ismem ? (conn.first == ID(DATA))
                                       : (conn.first == ID::Y);
                    if (!isout) continue;
                    RTLIL::SigSpec ms = sigmap(conn.second);
                    for (auto &ch : ms.chunks())
                        if (ch.wire) netw[cname(ch.wire->name.str())] = cell;
                }
            }
            for (auto *cell : sorted) {
                bool ismem = cell->type.str().compare(0, 6, "$memrd") == 0;
                std::set<RTLIL::Cell*> ps;
                for (auto &conn : cell->connections()) {
                    bool isout = ismem ? (conn.first == ID(DATA))
                                       : (conn.first == ID::Y);
                    if (isout) continue;
                    RTLIL::SigSpec ms = sigmap(conn.second);
                    for (auto &ch : ms.chunks()) {
                        if (!ch.wire) continue;
                        auto w = netw.find(cname(ch.wire->name.str()));
                        if (w != netw.end() && w->second != cell)
                            ps.insert(w->second);
                    }
                }
                for (auto *p : ps) { succ[p].push_back(cell); pred[cell]++; }
            }
            // hot-first Kahn: bucket by log2(count), original position ties
            auto key = [&](RTLIL::Cell *c) {
                long a = act[c]; int b = 0;
                while (a) { b++; a >>= 1; }
                return std::make_pair(-b, opos[c]);
            };
            std::set<std::pair<std::pair<int,int>, RTLIL::Cell*>> ready;
            for (auto *cell : sorted)
                if (pred[cell] == 0) ready.insert({key(cell), cell});
            std::vector<RTLIL::Cell*> order;
            order.reserve(sorted.size());
            while (!ready.empty()) {
                auto *c = ready.begin()->second;
                ready.erase(ready.begin());
                order.push_back(c);
                for (auto *sc : succ[c])
                    if (--pred[sc] == 0) ready.insert({key(sc), sc});
            }
            if (order.size() == sorted.size()) {
                sorted = order;
                fprintf(stderr, "GSM_ACTPROF: resorted %zu cells hot-first\n",
                        order.size());
            } else
                fprintf(stderr, "GSM_ACTPROF: resort FAILED (%zu != %zu), "
                        "keeping topo order\n", order.size(), sorted.size());
        }
    }
    if (g_gated) {
        const int K = g_gated_block;
        int idx = 0;
        for (auto *cell : sorted) g_gp.blk[cell] = idx++ / K;
        g_gp.nb = (idx + K - 1) / K;
        g_gp.nw = (g_gp.nb + 63) / 64;
        // Per-cell reads/writes over ALL chunks of every port.
        std::set<std::string> regnames, innames;
        for (auto &reg : registers) regnames.insert(reg.name);
        for (auto &w : mod->wires_)
            if (w.second->port_input) innames.insert(cname(w.second->name.str()));
        for (auto *cell : sorted) {
            int b = g_gp.blk[cell];
            // $memwr sits in `sorted` but is EMITTED by the seq phase — its
            // ADDR/DATA/EN wires are seq reads (must persist), NOT intra-block
            // comb reads.  Classifying them here made the memwr staging wires
            // function-locals: garbage whenever their producer block was
            // skipped (caught: first write vanished on the wide-word fixture).
            if (cell->type.str().compare(0, 6, "$memwr") == 0)
                continue;
            bool ismem = cell->type.str().compare(0, 6, "$memrd") == 0;
            for (auto &conn : cell->connections()) {
                bool isout = ismem ? (conn.first == ID(DATA))
                                   : (conn.first == ID::Y);
                RTLIL::SigSpec ms = sigmap(conn.second);
                for (auto &ch : ms.chunks()) {
                    if (!ch.wire) continue;
                    std::string n = cname(ch.wire->name.str());
                    if (isout) {
                        g_gp.writers[n].insert(b);
                        g_gp.bwidth[n] = ch.wire->width;
                    } else
                        g_gp.readers[n].insert(b);
                }
            }
            if (ismem)
                g_gp.memreaders[cname(cell->getParam(ID(MEMID))
                                          .decode_string())].insert(b);
        }
        // Seq-phase reads: every wire connected to a non-comb cell (flops,
        // memwr — CLK etc. included, harmless).
        std::set<std::string> seqreads;
        for (auto &c : mod->cells_) {
            auto *cell = c.second;
            if (g_gp.blk.count(cell)
                && cell->type.str().compare(0, 6, "$memwr") != 0) continue;
            for (auto &conn : cell->connections()) {
                RTLIL::SigSpec ms = sigmap(conn.second);
                for (auto &ch : ms.chunks())
                    if (ch.wire) seqreads.insert(cname(ch.wire->name.str()));
            }
        }
        // Output-copy reads too: gated sm_comb emits the output tail from the
        // persistent statics, so every net emit_outputs references must
        // survive its writer block being skipped.
        for (auto &w : mod->wires_) {
            if (!w.second->port_output) continue;
            RTLIL::SigSpec ms = sigmap(RTLIL::SigSpec(w.second));
            for (auto &ch : ms.chunks())
                if (ch.wire) seqreads.insert(cname(ch.wire->name.str()));
        }
        // Boundary = comb-written net whose value must survive its writer
        // block being skipped: read by another block, sliced across several
        // writer blocks, or read by the seq phase.  EVERY writer block gets
        // the change-compare (a slice write from any of them must propagate).
        g_gp.blk_cmp.assign(g_gp.nb, {});
        for (auto &p : g_gp.writers) {
            const std::string &n = p.first;
            if (regnames.count(n) || innames.count(n)) continue;
            const std::set<int> &wbs = p.second;
            bool cross = wbs.size() > 1;
            auto rit = g_gp.readers.find(n);
            if (!cross && rit != g_gp.readers.end())
                for (int rb : rit->second)
                    if (!wbs.count(rb)) { cross = true; break; }
            if (cross || seqreads.count(n)) {
                g_gp.boundary.insert(n);
                bool cmp = cross ||
                    (rit != g_gp.readers.end() && !rit->second.empty());
                if (cmp)
                    for (int wb : wbs) g_gp.blk_cmp[wb].push_back(n);
            }
        }
        // Emit the persistent storage: dirty words, boundary nets + shadows.
        fprintf(out, "// GSM_GATED persistent state (block dirty + boundary nets)\n");
        fprintf(out, "static uint64_t _bd[%d];\n", g_gp.nw);
        fprintf(out, "static int _gd_init;\n");
        fprintf(out, "static int _gd_wne(const %s*a,const %s*b,int n)"
                     "{for(int i=0;i<n;i++)if(a[i]!=b[i])return 1;return 0;}\n", lt(), lt());
        std::set<std::string> cmpset;
        for (auto &v : g_gp.blk_cmp) for (auto &n : v) cmpset.insert(n);
        for (auto &n : g_gp.boundary) {
            int w = g_gp.bwidth[n];
            bool cmp = cmpset.count(n) != 0;
            if (is_wide(w)) {
                fprintf(out, "static %s %s[%d];\n", lt(), n.c_str(), nlimbs(w));
                if (cmp) fprintf(out, "static %s _sh%s[%d];\n", lt(), n.c_str(), nlimbs(w));
            } else {
                fprintf(out, "static %s %s;\n", ctype(w), n.c_str());
                if (cmp) fprintf(out, "static %s _sh%s;\n", ctype(w), n.c_str());
            }
        }
        // prev copies for register-commit and primary-input compares
        for (auto &reg : registers)
            if (g_gp.readers.count(reg.name)) {
                if (is_wide(reg.width))
                    fprintf(out, "static %s _pv%s[%d];\n", lt(),
                            reg.name.c_str(), nlimbs(reg.width));
                else
                    fprintf(out, "static %s _pv%s;\n", ctype(reg.width),
                            reg.name.c_str());
            }
        for (auto &w : mod->wires_) {
            auto *wire = w.second;
            if (!wire->port_input) continue;
            std::string n = cname(wire->name.str());
            if (n == "_clk" || n == "_rst" || !g_gp.readers.count(n)) continue;
            if (is_wide(wire->width))
                fprintf(out, "static %s _pv%s[%d];\n", lt(), n.c_str(),
                        nlimbs(wire->width));
            else
                fprintf(out, "static %s _pv%s;\n", ctype(wire->width), n.c_str());
        }
        fprintf(out, "\n");
        fprintf(stderr, "GSM_GATED: %d blocks of %d, %zu boundary nets\n",
                g_gp.nb, K, g_gp.boundary.size());
    }

    // Input struct (primary inputs, excluding clk/rst)
    fprintf(out, "typedef struct {\n");
    bool has_inputs = false;
    bool saw_rst_port = false;
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_input) {
            std::string wn = cname(wire->name.str());
            if (wn == "_rst") saw_rst_port = true;
            if (wn != "_clk" && wn != "_rst") {
                if (is_wide(wire->width))
                    fprintf(out, "    %s %s[%d];  // %d bits\n", lt(),
                            wn.c_str(), nlimbs(wire->width), wire->width);
                else
                    fprintf(out, "    %s %s;  // %d bits\n", ctype(wire->width), wn.c_str(), wire->width);
                has_inputs = true;
            }
        }
    }
    if (!has_inputs) fprintf(out, "    int _dummy;\n");
    fprintf(out, "} inputs_t;\n\n");

    // Out-of-band reset handshake marker. Reset is a TWO-SIDED protocol:
    // this side strips a port named `rst` from inputs_t; nvc's bridge
    // diverts the same pin and drives sm_reset from AJB[5]. Removing
    // either half alone is silent poison (measured: 16/19 accelbench
    // designs wrong with both gates green). The bridge dlsym's this
    // marker at install and hard-declines on any disagreement, so a
    // one-sided edit fails loudly instead.
    fprintf(out, "const int sm_oob_reset = %d;\n\n", saw_rst_port ? 1 : 0);

    // Wide-word memories (>64b/word): words become limb arrays, stored flat
    // as name[depth*wnl]; $memrd copies the word's limbs, $memwr does a
    // per-limb masked RMW.  Init constants beyond 128 bits are unsupported
    // (MemInfo::init is __int128) — decline those loudly.
    for (auto &m : memories)
        if (m.second.width > 128 && !m.second.init.empty()) {
            fprintf(stderr, "gen_statemachine: memory %s has %d-bit words with"
                    " init values (>128-bit init unsupported) — declining\n",
                    m.second.name.c_str(), m.second.width);
            exit(1);
        }

    // State struct
    fprintf(out, "typedef struct {\n");
    for (auto &reg : registers) {
        if (is_wide(reg.width))
            fprintf(out, "    %s %s[%d];  // %d bits\n", lt(),
                    reg.name.c_str(), nlimbs(reg.width), reg.width);
        else
            fprintf(out, "    %s %s;  // %d bits\n", ctype(reg.width), reg.name.c_str(), reg.width);
    }
    for (auto &m : memories) {
        if (m.second.width > 64)
            fprintf(out, "    %s %s[%d];  // %d x %d-bit (%d limbs/word)\n", lt(),
                    m.second.name.c_str(), m.second.depth * nlimbs(m.second.width),
                    m.second.depth, m.second.width, nlimbs(m.second.width));
        else
            fprintf(out, "    uint64_t %s[%d];  // %d x %d-bit\n",
                    m.second.name.c_str(), m.second.depth, m.second.depth, m.second.width);
    }
    fprintf(out, "} state_t;\n\n");

    // FSM coverage struct — everything coverage-related is inside
    // #ifdef SM_FSM_COV in the EMITTED code: default builds carry no
    // dead .bss and device (nvcc) builds see no host-only stdio/globals.
    if (!fsms.empty()) {
        fprintf(out, "#ifdef SM_FSM_COV\n");
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
        fprintf(out, "static fsm_coverage_t sm_fsm_cov;\n");
        fprintf(out, "#endif // SM_FSM_COV\n\n");
    }

    // Output struct (observable signals)
    fprintf(out, "typedef struct {\n");
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_output) {
            if (is_wide(wire->width))
                fprintf(out, "    %s %s[%d];  // %d bits\n", lt(),
                        cname(wire->name.str()).c_str(), nlimbs(wire->width), wire->width);
            else
                fprintf(out, "    %s %s;  // %d bits\n",
                        ctype(wire->width), cname(wire->name.str()).c_str(), wire->width);
        }
    }
    fprintf(out, "} outputs_t;\n\n");

    // --- sliced (merged) register emission helpers ---------------------
    // Power-on limbs for a merged register: per-bit `init` attr if defined,
    // else the slice's async-reset value, else 0 (bits no slice covers = 0).
    auto merged_pov = [&](const RegInfo &reg) {
        const int LS = g_w64 ? 6 : 5, LM = g_w64 ? 63 : 31;
        std::vector<uint64_t> lv((size_t)nlimbs(reg.width), 0);
        for (auto &sl : reg.slices)
            for (int i = 0; i < sl.width; i++) {
                RTLIL::State st = RTLIL::Sx;
                if (sl.has_init && i < sl.init_const.size())
                    st = sl.init_const[i];
                if (st != RTLIL::S0 && st != RTLIL::S1)
                    st = (sl.has_arst && i < sl.arst_const.size())
                       ? sl.arst_const[i] : RTLIL::S0;
                if (st == RTLIL::S1) {
                    int p = sl.offset + i;
                    lv[p >> LS] |= UINT64_C(1) << (p & LM);
                }
            }
        return lv;
    };
    // Slice mask/value as 64-bit words (narrow merged registers, width <= 64)
    auto slice_mv64 = [&](const RegSlice &sl, const RTLIL::Const &c,
                          uint64_t &m, uint64_t &v) {
        m = v = 0;
        for (int i = 0; i < sl.width; i++) {
            uint64_t bit = 1ull << (sl.offset + i);
            m |= bit;
            if (i < c.size() && c[i] == RTLIL::S1) v |= bit;
        }
    };
    // Slice mask/value as per-limb vectors (wide merged registers)
    auto slice_mvw = [&](const RegInfo &reg, const RegSlice &sl,
                         const RTLIL::Const &c,
                         std::vector<uint64_t> &m, std::vector<uint64_t> &v) {
        const int LS = g_w64 ? 6 : 5, LM = g_w64 ? 63 : 31;
        m.assign((size_t)nlimbs(reg.width), 0);
        v.assign((size_t)nlimbs(reg.width), 0);
        for (int i = 0; i < sl.width; i++) {
            int p = sl.offset + i;
            m[p >> LS] |= UINT64_C(1) << (p & LM);
            if (i < c.size() && c[i] == RTLIL::S1)
                v[p >> LS] |= UINT64_C(1) << (p & LM);
        }
    };

    // Reset function
    fprintf(out, "void sm_reset(state_t *s) {\n");
    // Zero the whole state first: memory words with zero init are skipped
    // below (and registers rely on it for padding limbs), so uninitialized
    // storage (stack-allocated state_t) read back garbage — measured: a
    // proc_rom's entry 0 returned 0x02 on the rom_bench differential.
    fprintf(out, "    for (unsigned long _zi = 0; _zi < sizeof(state_t); _zi++)\n"
                 "        ((char*)s)[_zi] = 0;\n");
    for (auto &reg : registers) {
        if (!reg.slices.empty()) {
            // merged sliced register: compose per-slice init/arst at offsets
            std::vector<uint64_t> lv = merged_pov(reg);
            if (is_wide(reg.width)) {
                for (int l = 0; l < (int)lv.size(); l++) {
                    if (g_w64)
                        fprintf(out, "    s->%s[%d] = UINT64_C(0x%llx);\n",
                                reg.name.c_str(), l, (unsigned long long)lv[l]);
                    else
                        fprintf(out, "    s->%s[%d] = 0x%xu;\n",
                                reg.name.c_str(), l, (unsigned)lv[l]);
                }
            } else {
                unsigned __int128 pv = lv[0];
                if (!g_w64 && lv.size() > 1) pv |= (unsigned __int128)lv[1] << 32;
                fprintf(out, "    s->%s = %s;\n",
                        reg.name.c_str(), u128_lit(pv).c_str());
            }
            continue;
        }
        // Power-on value: the reg's own init if it has one, else the async
        // reset value (a reg with an async reset but no explicit init powers on
        // at its reset value, the prior behavior).
        unsigned __int128 pov = reg.has_init ? reg.init_val : reg.arst_val;
        if (is_wide(reg.width)) {
            int ny = nlimbs(reg.width);
            for (int l = 0; l < ny; l++) {
                if (g_w64) {
                    uint64_t lw = (l < 2) ? (uint64_t)(pov >> (64 * l)) : 0;
                    fprintf(out, "    s->%s[%d] = UINT64_C(0x%llx);\n",
                            reg.name.c_str(), l, (unsigned long long)lw);
                    continue;
                }
                uint32_t lw = (l < 4) ? (uint32_t)(pov >> (32 * l)) : 0;
                fprintf(out, "    s->%s[%d] = 0x%xu;\n", reg.name.c_str(), l, lw);
            }
        } else
            fprintf(out, "    s->%s = %s;\n",
                    reg.name.c_str(), u128_lit(pov).c_str());
    }
    for (auto &m : memories) {
        auto &mem = m.second;
        const bool ww = mem.width > 64;
        const int wnl = ww ? nlimbs(mem.width) : 1;
        for (int i = 0; i < mem.depth; i++) {
            auto it = mem.init.find(i);
            unsigned __int128 val = (it != mem.init.end()) ? it->second : 0;
            if (val == 0) continue;
            if (ww) {
                const int lb = g_w64 ? 64 : 32;
                for (int l = 0; l < wnl && l * lb < 128; l++) {
                    uint64_t lw = (uint64_t)(val >> (lb * l));
                    if (lb == 32) lw &= 0xffffffffu;
                    if (lw)
                        fprintf(out, "    s->%s[%d] = %s0x%llx%s;\n",
                                mem.name.c_str(), i * wnl + l,
                                lb == 64 ? "UINT64_C(" : "",
                                (unsigned long long)lw,
                                lb == 64 ? ")" : "u");
                }
            } else
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
    // ---- output-cone analysis (for the fused sm_clock_out) ------------
    // Cells reachable backward from the output ports, stopping at register
    // Q bits and input ports. After emit_seq commits the registers, these
    // cells recomputed with REFRESHED register aliases yield the POST-edge
    // outputs — replacing the full second sm_comb pass at the posedge eval.
    pool<RTLIL::Cell*> outcone;
    pool<std::string>  outcone_regs;
    // Dead-output pruning: which outputs each cone cell feeds (bit per output
    // in emission order; >64 outputs -> masks saturate to ~0 = always shared).
    // The bridge writes the exported `sm_live_outputs` mask after inspecting
    // which output nexuses actually have readers; cells exclusive to dead
    // outputs are skipped at run time (anything feeding a shared cell is
    // itself shared, so the shared/bucket partition preserves topo order).
    struct FeedMask { uint64_t w[4] = {0,0,0,0};
        void set(int i){ if(i<256) w[i>>6] |= 1ull<<(i&63); else w[0]=w[1]=w[2]=w[3]=~0ull; }
        bool has(int i) const { return i<256 ? (w[i>>6]>>(i&63))&1 : true; }
        int pop() const { int n=0; for(int k=0;k<4;k++) n+=__builtin_popcountll(w[k]); return n; }
        void sat(){ w[0]=w[1]=w[2]=w[3]=~0ull; } };
    dict<RTLIL::Cell*, FeedMask> feeds;
    std::vector<RTLIL::Wire*> out_order;
    {
        dict<RTLIL::SigBit, RTLIL::Cell*> bdrv;
        for (auto *cell : sorted)
            for (auto &conn : cell->connections())
                if (cell->output(conn.first)) {
                    RTLIL::SigSpec os = sigmap(conn.second);
                    for (auto &b : os) if (b.wire) bdrv[b] = cell;
                }
        dict<RTLIL::SigBit, RTLIL::SigBit> connmap;
        for (auto &cn : mod->connections()) {
            RTLIL::SigSpec ls = sigmap(cn.first), rs = sigmap(cn.second);
            int n = std::min(ls.size(), rs.size());
            for (int ci = 0; ci < n; ci++)
                if (ls[ci].wire && rs[ci].wire) connmap[ls[ci]] = rs[ci];
        }
        for (auto &w2 : mod->wires_)
            if (w2.second->port_output) out_order.push_back(w2.second);
        int oi = 0;
        for (auto *ow : out_order) {
            const int obit = oi;   // FeedMask index; >=256 saturates
            oi++;
            std::vector<RTLIL::SigBit> wl;
            pool<RTLIL::SigBit> seen;
            RTLIL::SigSpec os = sigmap(RTLIL::SigSpec(ow));
            for (auto &b : os) if (b.wire) wl.push_back(b);
            while (!wl.empty()) {
                RTLIL::SigBit b = wl.back(); wl.pop_back();
                if (!b.wire || seen.count(b)) continue;
                seen.insert(b);
                auto rq = g_regbit.find(b);
                if (rq != g_regbit.end()) { outcone_regs.insert(rq->second.first); continue; }
                if (b.wire->port_input) continue;
                auto cm = connmap.find(b);
                if (cm != connmap.end()) wl.push_back(cm->second);
                auto dv = bdrv.find(b);
                if (dv == bdrv.end()) continue;
                RTLIL::Cell *c = dv->second;
                FeedMask &fm = feeds[c];
                if (fm.has(obit) && outcone.count(c)) continue;
                fm.set(obit);
                if (!outcone.count(c)) outcone.insert(c);
                for (auto &conn : c->connections())
                    if (!c->output(conn.first)) {
                        RTLIL::SigSpec is = sigmap(conn.second);
                        for (auto &ib : is) if (ib.wire) wl.push_back(ib);
                    }
            }
        }
    }
    // Cells feeding the D/EN/ARST of the LATE (extra-clock-group) registers:
    // sm_clock_late needs only THIS cone evaluated from the snapshot — for
    // designs whose gated clocks were internalized (whole-core chunks) it is
    // a handful of cells, replacing a full-network pass per gated edge.
    pool<RTLIL::Cell*> latecone;
    {
        dict<RTLIL::SigBit, RTLIL::Cell*> bdrv;
        for (auto *cell : sorted)
            for (auto &conn : cell->connections())
                if (cell->output(conn.first)) {
                    RTLIL::SigSpec os = sigmap(conn.second);
                    for (auto &b : os) if (b.wire) bdrv[b] = cell;
                }
        dict<RTLIL::SigBit, RTLIL::SigBit> connmap;
        for (auto &cn : mod->connections()) {
            RTLIL::SigSpec ls = sigmap(cn.first), rs = sigmap(cn.second);
            int n = std::min(ls.size(), rs.size());
            for (int ci = 0; ci < n; ci++)
                if (ls[ci].wire && rs[ci].wire) connmap[ls[ci]] = rs[ci];
        }
        std::vector<RTLIL::SigBit> wl;
        pool<RTLIL::SigBit> seen;
        auto push_sig = [&](const RTLIL::SigSpec &sg) {
            RTLIL::SigSpec ms = sigmap(sg);
            for (auto &b : ms) if (b.wire) wl.push_back(b);
        };
        for (auto &r : registers)
            if (r.clk_group > 0) {
                push_sig(r.d_sig);
                if (r.en_sig.size()) push_sig(r.en_sig);
                if (r.arst_sig.size()) push_sig(r.arst_sig);
                for (auto &sl : r.slices) {
                    push_sig(sl.d_sig);
                    if (sl.en_sig.size()) push_sig(sl.en_sig);
                    if (sl.arst_sig.size()) push_sig(sl.arst_sig);
                }
            }
        while (!wl.empty()) {
            RTLIL::SigBit b = wl.back(); wl.pop_back();
            if (!b.wire || seen.count(b)) continue;
            seen.insert(b);
            if (g_regbit.count(b)) continue;
            if (b.wire->port_input) continue;
            auto cm = connmap.find(b);
            if (cm != connmap.end()) wl.push_back(cm->second);
            auto dv = bdrv.find(b);
            if (dv == bdrv.end()) continue;
            RTLIL::Cell *c = dv->second;
            if (!latecone.count(c)) {
                latecone.insert(c);
                for (auto &conn : c->connections())
                    if (!c->output(conn.first)) {
                        RTLIL::SigSpec is = sigmap(conn.second);
                        for (auto &ib : is) if (ib.wire) wl.push_back(ib);
                    }
            }
        }
    }
    std::map<std::string,int> regwidth;
    for (auto &r : registers) regwidth[r.name] = r.width;

    // one cell's C emission (shared by the full pass and the cone recompute)
    auto emit_cell = [&](RTLIL::Cell *cell) {
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
                auto mit = memories.find(memid);
                int mw = (mit != memories.end()) ? mit->second.width : 64;
                // DATA may be a CONCATENATION of distinct wires or a partial
                // slice: yosys proc_rom packs several case-output signals into
                // one ROM word (Vortex decode packed amo_unsigned+4 siblings
                // into a 5-bit word; a bare first_chunk = mem[addr] handed the
                // whole word to chunk 0 — a 1-bit reg read back 4 — and left
                // the rest dead-0).  Scatter the word chunk-by-chunk, same as
                // the multi-wire-Y path below.
                auto dch = data_port.chunks();
                int dnwire = 0; bool dpartial = false;
                for (auto &c : dch) {
                    if (c.wire) dnwire++;
                    if (c.wire && (c.offset != 0 || c.width != c.wire->width))
                        dpartial = true;
                }
                bool dscatter = (dnwire > 1 || dpartial);
                if (mw > 64) {
                    if (dscatter) {
                        fprintf(stderr, "gen_statemachine: $memrd wide DATA is"
                                " a multi-chunk/partial concat (%s) —"
                                " declining (a first-chunk wcopy would"
                                " miscompute)\n", memid.c_str());
                        exit(1);
                    }
                    int wnl = nlimbs(mw);
                    auto *dw = (*cell->getPort(ID(DATA)).chunks().begin()).wire;
                    int dnl = nlimbs(dw->width);
                    fprintf(out, "    wcopy(%s, &s->%s[(%s & %s) * %d], %d);\n",
                            data_name.c_str(), cname(memid).c_str(),
                            addr.c_str(), mask_lit(abits).c_str(), wnl, wnl);
                    for (int l = wnl; l < dnl; l++)
                        fprintf(out, "    %s[%d] = 0;\n", data_name.c_str(), l);
                } else if (dscatter) {
                    fprintf(out, "    { uint64_t _yspl = s->%s[%s & %s];\n",
                            cname(memid).c_str(), addr.c_str(),
                            mask_lit(abits).c_str());
                    int pos = 0;
                    for (auto &ch : dch) {
                        if (ch.wire) {
                            std::string wn = cname(ch.wire->name.str());
                            int w = ch.width, off = ch.offset, ww = ch.wire->width;
                            if (is_wide(ww))
                                fprintf(out, "      wplacew_s(%s,%d,_yspl,%d,%d);\n",
                                        wn.c_str(), off, pos, w);
                            else if (off == 0 && w == ww)
                                fprintf(out, "      %s = (_yspl >> %d) & %s;\n",
                                        wn.c_str(), pos, mask_lit(w).c_str());
                            else {
                                // same u32 mask-lift hazard as the Y scatter
                                std::string m = mask_lit(w);
                                if (g_u32 && ww > 32)
                                    m = "(uint64_t)" + m;
                                fprintf(out, "      %s = (%s & ~(%s << %d)) |"
                                        " (((_yspl >> %d) & %s) << %d);\n",
                                        wn.c_str(), wn.c_str(), m.c_str(), off,
                                        pos, m.c_str(), off);
                            }
                        }
                        pos += ch.width;
                    }
                    fprintf(out, "    }\n");
                } else
                    fprintf(out, "    %s = s->%s[%s & %s];\n",
                            data_name.c_str(), cname(memid).c_str(),
                            addr.c_str(), mask_lit(abits).c_str());
            }
            return;
        }

        if (y_name.empty()) return;

        // Wide cell: any operand, the Y slice, OR the TARGET WIRE exceeds 64
        // bits. (A narrow slice of a wide wire — a partial drive — must still go
        // wide, else we'd emit `wide_array = scalar`.) The scalar chain below is
        // left byte-identical and only runs for fully-narrow cells.
        int aw_ = cell->hasPort(ID::A) ? cell->getPort(ID::A).size() : 0;
        int bw_ = cell->hasPort(ID::B) ? cell->getPort(ID::B).size() : 0;
        if (is_wide(ywire_w) || is_wide(y_width) || is_wide(aw_) || is_wide(bw_)) {
            emit_wide_cell(out, cell, sigmap, y_name, y_width, y_off,
                           is_wide(ywire_w), aw_, bw_);
            return;
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
            int nwire = 0; bool partial = false;
            for (auto &c : yc) {
                if (c.wire) nwire++;
                // A chunk that is a partial slice of its wire (offset!=0 or narrower
                // than the wire) can't be a bare `wire = expr` — that writes the value
                // at bit 0 and clobbers the untouched bits (e.g. VeeR's per-slice
                // `dec_tlu_packet_e4[24:20] = ...` assigns). Route it through the
                // scatter, which RMW-places each chunk at its own offset.
                if (c.wire && (c.offset != 0 || c.width != c.wire->width)) partial = true;
            }
            if (nwire > 1 || partial) { y_chunks.assign(yc.begin(), yc.end()); y_multi = true; }
        }
        if (y_multi) { fprintf(out, "    { uint64_t _yspl = 0;\n"); y_name = "_yspl"; }

        // GSM_U32: a u32-carried operand loses the carry/product/negate
        // headroom a wider Y needs; pre-wrap the first operand so C
        // promotion carries the whole expression (emission unchanged when
        // the mode is off).
        auto upwrap = [&](const std::string &e) {
            return (g_u32 && y_width > 32) ? "(uint64_t)(" + e + ")" : e;
        };
        if (type == "$add") {
            fprintf(out, "    %s = (%s + %s) & %s;\n",
                    y_name.c_str(),
                    upwrap(sig_expr(cell->getPort(ID::A), sigmap)).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    masks.c_str());
        } else if (type == "$sub") {
            fprintf(out, "    %s = (%s - %s) & %s;\n",
                    y_name.c_str(),
                    upwrap(sig_expr(cell->getPort(ID::A), sigmap)).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    masks.c_str());
        } else if (type == "$and" && cell->get_bool_attribute(ID(gsm_icg_clk))) {
            // icg2en exported gated clock: real clk (bridge-poked) & the hold
            // register that froze the enable at the last base posedge -- the
            // exact ICG output waveform (0 through clk-low, held through
            // clk-high; never tracks a mid-phase enable change).
            fprintf(out, "    %s = (sm_icg_clkval & 1) & %s;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str());
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
            // C `<<` on a 64-bit scalar is UNDEFINED for a count >= 64 (x86
            // masks it to &63, so `acc sll 65` wrongly yields acc<<1 instead of
            // 0). yosys shift counts are unsigned and can exceed 63 (variable
            // `sll shamt`, shamt up to the operand's full range). Guard >=64->0;
            // the width mask already handles the [width,63] range.
            std::string shl_a = sig_expr(cell->getPort(ID::A), sigmap);
            if (g_u32)
                shl_a = "(uint64_t)(" + shl_a + ")";
            fprintf(out, "    { uint64_t _s=(uint64_t)(%s); %s = (_s>=64 ? 0 : "
                    "((%s) << _s)) & %s; }\n",
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    y_name.c_str(), shl_a.c_str(), masks.c_str());
        } else if (type == "$shr") {
            // Same C shift-count UB guard as $shl (see above); logical right
            // shift fills 0, so a count >= 64 (or >= width) yields 0.
            fprintf(out, "    { uint64_t _s=(uint64_t)(%s); %s = (_s>=64 ? 0 : "
                    "((%s) >> _s)); }\n",
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str());
        } else if (type == "$sshr") {
            // Arithmetic right shift: sign-extend A to its own width first;
            // a count >= width floods with the sign bit (clamp to 63 after
            // extension — the extended value's sign fills everything above).
            int aw2 = cell->getPort(ID::A).size();
            std::string sa = signed_expr(sig_expr(cell->getPort(ID::A), sigmap), aw2);
            fprintf(out, "    { uint64_t _s=(uint64_t)(%s); if (_s>63) _s=63; "
                    "%s = ((uint64_t)(%s >> _s)) & %s; }\n",
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    y_name.c_str(), sa.c_str(), masks.c_str());
        } else if (type == "$sshl") {
            // Shift left is sign-agnostic; same count-UB guard as $shl.
            fprintf(out, "    { uint64_t _s=(uint64_t)(%s); %s = (_s>=64 ? 0 : "
                    "(((%s) << _s) & %s)); }\n",
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(),
                    y_name.c_str(),
                    upwrap(sig_expr(cell->getPort(ID::A), sigmap)).c_str(),
                    masks.c_str());
        } else if (type == "$shift" || type == "$shiftx") {
            // Dynamic-offset shift / indexed part-select a[b +: w]: Y = A >> B,
            // where B is the (possibly signed) offset — a negative B shifts LEFT
            // by -B. yosys emits $shift/$shiftx (NOT $shr) whenever a signal is
            // used as a part-select/bit-select offset; the fill is x for $shiftx
            // / 0 for $shift, which in 2-state is 0. Previously unhandled, so Y
            // silently stayed at its init 0 (e.g. VeeR ifu
            // dec_tlu_mrac_ff[cacheable_select] -> ifc_fetch_uncacheable always 1,
            // lsu dec_tlu_mrac_ff[csr_idx] -> is_sideeffects wrong).
            auto a = sig_expr(cell->getPort(ID::A), sigmap);
            auto b = sig_expr(cell->getPort(ID::B), sigmap);
            bool bs = cell->getParam(ID::B_SIGNED).as_bool();
            int bw = cell->getPort(ID::B).size();
            if (bs && bw < 64)
                fprintf(out, "    { int64_t _o=(int64_t)(uint64_t)(%s);"
                        " if(_o&(INT64_C(1)<<%d)) _o-=(INT64_C(1)<<%d);"
                        " %s = ((_o<=-64||_o>=64)?0:(_o>=0?((uint64_t)(%s)>>_o)"
                        ":((uint64_t)(%s)<<(-_o)))) & %s; }\n",
                        b.c_str(), bw-1, bw, y_name.c_str(), a.c_str(), a.c_str(),
                        masks.c_str());
            else
                fprintf(out, "    { uint64_t _o=(uint64_t)(%s);"
                        " %s = (_o>=64?0:((uint64_t)(%s)>>_o)) & %s; }\n",
                        b.c_str(), y_name.c_str(), a.c_str(), masks.c_str());
        } else if (type == "$eq" || type == "$eqx") {
            // $eqx (===): exact 4-state equality == plain equality in 2-state
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
        } else if (type == "$ne" || type == "$nex") {
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
                        upwrap(sig_expr(cell->getPort(ID::A), sigmap)).c_str(),
                        sig_expr(cell->getPort(ID::B), sigmap).c_str(), masks.c_str());
        } else if (type == "$neg") {
            fprintf(out, "    %s = (-%s) & %s;\n", y_name.c_str(),
                    upwrap(sig_expr(cell->getPort(ID::A), sigmap)).c_str(),
                    masks.c_str());
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
        } else if (type == "$logic_and" || type == "$logic_or") {
            fprintf(out, "    %s = ((%s != 0) %s (%s != 0)) ? 1 : 0;\n",
                    y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    type == "$logic_and" ? "&&" : "||",
                    sig_expr(cell->getPort(ID::B), sigmap).c_str());
        } else if (type == "$xnor") {
            fprintf(out, "    %s = (~(%s ^ %s)) & %s;\n", y_name.c_str(),
                    sig_expr(cell->getPort(ID::A), sigmap).c_str(),
                    sig_expr(cell->getPort(ID::B), sigmap).c_str(), masks.c_str());
        } else {
            fprintf(stderr, "gen_statemachine: unhandled cell type %s —"
                    " declining (a silent stub would leave its output 0 and"
                    " miscompute)\n", type.c_str());
            exit(1);   // install checks the exit code; chunk stays interpreted
        }
        if (y_multi) {
            int pos = 0;
            for (auto &ch : y_chunks) {
                if (ch.wire) {
                    std::string wn = cname(ch.wire->name.str());
                    int w = ch.width, off = ch.offset, ww = ch.wire->width;
                    if (is_wide(ww))   // chunk targets a limb-array wire
                        fprintf(out, "      wplacew_s(%s,%d,_yspl,%d,%d);\n",
                                wn.c_str(), off, pos, w);
                    else if (off == 0 && w == ww)
                        fprintf(out, "      %s = (_yspl >> %d) & %s;\n",
                                wn.c_str(), pos, mask_lit(w).c_str());
                    else {
                        // Slice-width mask into a WIDER destination:
                        // under GSM_U32 a narrow mask literal is 32-bit.
                        // Two hazards for a u64 destination: off+w > 32
                        // makes the shift UB, and — subtler — ~(mask<<off)
                        // of a 32-bit value ZERO-EXTENDS, wiping the
                        // destination's high 32 bits on every LOW-slice
                        // writeback (bug #4: i0_dp bit 43 lost to a
                        // ~(0x1fU<<19) at bits 19..23).  Lift whenever the
                        // destination is wider than 32, any offset.
                        std::string m = mask_lit(w);
                        if (g_u32 && ww > 32)
                            m = "(uint64_t)" + m;
                        fprintf(out, "      %s = (%s & ~(%s << %d)) |"
                                " (((_yspl >> %d) & %s) << %d);\n",
                                wn.c_str(), wn.c_str(), m.c_str(), off, pos, m.c_str(), off);
                    }
                }
                pos += ch.width;
            }
            fprintf(out, "    }\n");
        }
        };

    // Emit the output-cone cells: shared cells (feeding >1 output — anything
    // feeding a shared cell is itself shared, so topo order is preserved)
    // unconditionally, then each output's exclusive bucket guarded by its
    // sm_live_outputs bit.
    auto emit_cell_cns = [&](RTLIL::Cell *cell) {
        emit_cell(cell);
        if (g_census && g_census_active && cell->hasPort(ID::Y)) {
            auto &y = cell->getPort(ID::Y);
            if (y.chunks().begin() != y.chunks().end()
                && (*y.chunks().begin()).wire) {
                RTLIL::SigChunk yc = *y.chunks().begin();
                if (!is_wide(yc.wire->width))
                    fprintf(out, "    _gh[%d] ^= ((uint64_t)(%s) + %uULL) * 0x9E3779B97F4A7C15ULL;\n",
                            cns_gid_of(cell), cname(yc.wire->name.str()).c_str(),
                            cns_salt_of(cell));
            }
        }
    };

    auto emit_outcone_cells = [&]() {
        FeedMask sat; sat.sat();
        for (auto *cell : sorted)
            if (outcone.count(cell)) {
                const FeedMask &fm = feeds.count(cell) ? feeds.at(cell) : sat;
                if (fm.pop() != 1) emit_cell(cell);
            }
        for (size_t oi = 0; oi < out_order.size() && oi < 256; oi++) {
            bool hdr = false;
            for (auto *cell : sorted) {
                if (!outcone.count(cell)) continue;
                const FeedMask &fm = feeds.count(cell) ? feeds.at(cell) : sat;
                if (fm.pop() != 1 || !fm.has((int)oi)) continue;
                if (!hdr) {
                    fprintf(out, "    if (sm_live_outputs[%d] & (1ull<<%d)) {\n",
                            (int)(oi >> 6), (int)(oi & 63));
                    hdr = true;
                }
                emit_cell(cell);
            }
            if (hdr) fprintf(out, "    }\n");
        }
    };

    // GSM_ACTIVITY_CENSUS pre-pass: fix the group table BEFORE emission so
    // the _gh array size is known at declaration time.
    if (g_census)
        for (auto &c : mod->cells_)
            (void)cns_gid_of(c.second);

    auto emit_comb_prefix = [&](const char *sp) {
    if (g_census)
        fprintf(out, "    uint64_t _gh[_CNS_NG] = {0};\n");
    fprintf(out, "    // Input aliases\n");
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_input) {
            std::string wn = cname(wire->name.str());
            if (wn == "_clk" || wn == "_rst")
                fprintf(out, "    uint64_t %s = 0;  // clock/reset handled externally\n", wn.c_str());
            else if (is_wide(wire->width))
                fprintf(out, "    %s %s[%d]; wcopy(%s,in->%s,%d);\n", lt(),
                        wn.c_str(), nlimbs(wire->width), wn.c_str(), wn.c_str(), nlimbs(wire->width));
            else
                fprintf(out, "    %s %s = in->%s;\n", ctype(wire->width), wn.c_str(), wn.c_str());
        }
    }
    fprintf(out, "\n    // Register aliases (current state)\n");
    for (auto &reg : registers) {
        if (is_wide(reg.width))
            fprintf(out, "    %s %s[%d]; wcopy(%s,%s->%s,%d);\n", lt(),
                    reg.name.c_str(), nlimbs(reg.width), reg.name.c_str(), sp, reg.name.c_str(), nlimbs(reg.width));
        else if (g_spec_value >= 0 && reg.name == g_spec_regname)
            // FSM-SPEC: this state reg is a compile-time constant in this variant
            fprintf(out, "    %s %s = %ldu;  // FSM-SPEC state constant\n",
                    ctype(reg.width), reg.name.c_str(), g_spec_value);
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
            if (!reg.slices.empty()) {
                // merged sliced register: each slice's async reset forces only
                // ITS bit range (masked RMW), in both the local snapshot and
                // the persistent state — same level-sensitive semantics as the
                // whole-wire path below.
                for (auto &sl : reg.slices) {
                    if (sl.arst_expr.empty()) continue;
                    if (!hdr) { fprintf(out, "    // Async reset overrides\n"); hdr = true; }
                    if (is_wide(reg.width)) {
                        std::vector<uint64_t> m, v;
                        slice_mvw(reg, sl, sl.arst_const, m, v);
                        fprintf(out, "    if (%s) {", sl.arst_expr.c_str());
                        for (int l = 0; l < (int)m.size(); l++) {
                            if (!m[l]) continue;
                            if (g_w64)
                                fprintf(out,
                                    " %s[%d]=(%s[%d]&~UINT64_C(0x%llx))|UINT64_C(0x%llx);"
                                    " %s->%s[%d]=(%s->%s[%d]&~UINT64_C(0x%llx))|UINT64_C(0x%llx);",
                                    reg.name.c_str(), l, reg.name.c_str(), l,
                                    (unsigned long long)m[l], (unsigned long long)v[l],
                                    sp, reg.name.c_str(), l,
                                    sp, reg.name.c_str(), l,
                                    (unsigned long long)m[l], (unsigned long long)v[l]);
                            else
                                fprintf(out, " %s[%d]=(%s[%d]&~0x%xu)|0x%xu;"
                                         " %s->%s[%d]=(%s->%s[%d]&~0x%xu)|0x%xu;",
                                    reg.name.c_str(), l, reg.name.c_str(), l,
                                    (unsigned)m[l], (unsigned)v[l],
                                    sp, reg.name.c_str(), l,
                                    sp, reg.name.c_str(), l,
                                    (unsigned)m[l], (unsigned)v[l]);
                        }
                        fprintf(out, " }\n");
                    } else {
                        uint64_t m, v;
                        slice_mv64(sl, sl.arst_const, m, v);
                        fprintf(out, "    if (%s) { %s = (%s & ~UINT64_C(0x%llx))"
                                     " | UINT64_C(0x%llx); %s->%s = (%s->%s & "
                                     "~UINT64_C(0x%llx)) | UINT64_C(0x%llx); }\n",
                                sl.arst_expr.c_str(),
                                reg.name.c_str(), reg.name.c_str(),
                                (unsigned long long)m, (unsigned long long)v,
                                sp, reg.name.c_str(), sp, reg.name.c_str(),
                                (unsigned long long)m, (unsigned long long)v);
                    }
                }
                continue;
            }
            if (reg.arst_expr.empty()) continue;
            if (!hdr) { fprintf(out, "    // Async reset overrides\n"); hdr = true; }
            if (is_wide(reg.width)) {
                int ny = nlimbs(reg.width);
                fprintf(out, "    if (%s) {", reg.arst_expr.c_str());
                for (int l = 0; l < ny; l++) {
                    if (g_w64) {
                        uint64_t lw = (l < 2) ? (uint64_t)(reg.arst_val >> (64*l)) : 0;
                        fprintf(out, " %s[%d]=UINT64_C(0x%llx); %s->%s[%d]=UINT64_C(0x%llx);",
                                reg.name.c_str(), l, (unsigned long long)lw,
                                sp, reg.name.c_str(), l, (unsigned long long)lw);
                        continue;
                    }
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
        if (g_emit_gated_body && g_gp.boundary.count(wn)) {
            declared.insert(wn);   // binds to the file-scope static
            continue;
        }
        if (!declared.count(wn)) {
            // Gated body: a written non-boundary net is intra-block only and
            // topo order guarantees write-before-read, so skip the zero-init
            // (a skipped block's local is garbage nobody reads).  Undriven
            // nets keep the 0.
            bool noinit = g_emit_gated_body && g_gp.writers.count(wn);
            if (is_wide(wire->width))
                fprintf(out, noinit ? "    %s %s[%d];\n"
                                    : "    %s %s[%d] = {0};\n",
                        lt(), wn.c_str(), nlimbs(wire->width));
            else if (noinit)
                fprintf(out, "    %s %s;\n", ctype(wire->width), wn.c_str());
            else
                fprintf(out, "    %s %s = 0;\n", ctype(wire->width), wn.c_str());
            declared.insert(wn);
        }
    }
    fprintf(out, "\n");

    };  // end emit_comb_prefix

    auto emit_comb = [&](const char *sp) {
    emit_comb_prefix(sp);
    // Emit combinational logic in topological order
    fprintf(out, "    // Combinational evaluation (topologically sorted)\n");
    if (g_emit_gated_body) {
        // First call: everything dirty.
        fprintf(out, "    if (!_gd_init) { _gd_init = 1; "
                     "for (int _i = 0; _i < %d; _i++) _bd[_i] = ~0ull; }\n",
                g_gp.nw);
        // Primary-input change compares.
        fprintf(out, "    // input-change dirtying\n");
        for (auto &w : mod->wires_) {
            auto *wire = w.second;
            if (!wire->port_input) continue;
            std::string n = cname(wire->name.str());
            if (n == "_clk" || n == "_rst") continue;
            auto rit = g_gp.readers.find(n);
            if (rit == g_gp.readers.end()) continue;
            if (is_wide(wire->width)) {
                int nl = nlimbs(wire->width);
                fprintf(out, "    if (_gd_wne(_pv%s, in->%s, %d)) { "
                             "wcopy(_pv%s, in->%s, %d);\n",
                        n.c_str(), n.c_str(), nl, n.c_str(), n.c_str(), nl);
            } else
                fprintf(out, "    if (_pv%s != in->%s) { _pv%s = in->%s;\n",
                        n.c_str(), n.c_str(), n.c_str(), n.c_str());
            gd_emit_or(out, rit->second, -1, "        ");
            fprintf(out, "    }\n");
        }
        // Blocks: test-and-clear dirty bit, cells, boundary compares.
        // Word-level skip is sound: a bit in word w can only be set mid-pass
        // by an earlier block — cross-word setters run before the pass
        // reaches w, and a same-word setter implies w was nonzero at entry.
        int idx = 0;
        for (int b = 0; b < g_gp.nb; b++) {
            if ((b & 63) == 0) {
                if (b) fprintf(out, "    }\n");
                fprintf(out, "    if (_bd[%d]) {\n", b >> 6);
            }
            fprintf(out, "    if (_bd[%d] & (1ull<<%d)) { _bd[%d] &= ~(1ull<<%d);\n",
                    b >> 6, b & 63, b >> 6, b & 63);
            for (; idx < (int)sorted.size() && g_gp.blk[sorted[idx]] == b; idx++)
                emit_cell(sorted[idx]);
            for (auto &n : g_gp.blk_cmp[b]) {
                int w = g_gp.bwidth[n];
                if (is_wide(w)) {
                    int nl = nlimbs(w);
                    fprintf(out, "    if (_gd_wne(%s, _sh%s, %d)) { "
                                 "wcopy(_sh%s, %s, %d);\n",
                            n.c_str(), n.c_str(), nl, n.c_str(), n.c_str(), nl);
                } else
                    fprintf(out, "    if (%s != _sh%s) { _sh%s = %s;\n",
                            n.c_str(), n.c_str(), n.c_str(), n.c_str());
                gd_emit_or(out, g_gp.readers[n], b, "        ");
                fprintf(out, "    }\n");
            }
            fprintf(out, "    }\n");
        }
        if (g_gp.nb) fprintf(out, "    }\n");
    } else {
    g_census_active = true;
    for (auto *cell : sorted) emit_cell_cns(cell);
    g_census_active = false;
    }
    if (g_census)
        fprintf(out,
            "    _cns_calls++;\n"
            "    for (int _g = 0; _g < _CNS_NG; _g++) {\n"
            "        if (_gh[_g] != _cns_shadow[_g]) {\n"
            "            _cns_shadow[_g] = _gh[_g];\n"
            "            _cns_runs[_g]++;\n"
            "        }\n"
            "    }\n");

    fprintf(out, "\n");
    };  // end emit_comb lambda

    // Register commits + memory writes + FSM coverage (the sm_clock tail).
    // sp = write-pointer ("s" single-clock; "dst" masked). masked=false emits the
    // ORIGINAL unguarded text (single-clock byte-identical). masked=true guards
    // each commit by its clock group's posedge_mask bit.
    auto emit_seq = [&](const char *sp, bool masked) {
    fprintf(out, "    // Register updates (next state)\n");
    for (auto &reg : registers) {
        if (!reg.slices.empty()) {
            // merged sliced register: each slice commits as a masked RMW of
            // its own bit range under ITS OWN [group-bit &&] enable && !arst
            // gating. Slices are disjoint, so commit order is irrelevant; D
            // reads the local (pre-edge) aliases, so NBA semantics hold.
            for (auto &sl : reg.slices) {
                if (!sl.src.empty()) {
                    auto colon = sl.src.rfind(':');
                    if (colon != std::string::npos) {
                        std::string file = sl.src.substr(0, colon);
                        int line = 0;
                        sscanf(sl.src.c_str() + colon + 1, "%d", &line);
                        if (line > 0)
                            fprintf(out, "#line %d \"%s\"\n", line, file.c_str());
                    }
                }
                std::string dst = std::string(sp) + "->" + reg.name;
                std::string cond;
                if (masked) {
                    char g[40];
                    snprintf(g, sizeof g, "(posedge_mask & (1u<<%d))", reg.clk_group);
                    cond = g;
                    if (!sl.en_expr.empty()) cond += " && (" + sl.en_expr + ")";
                } else cond = sl.en_expr;
                if (!sl.arst_expr.empty()) {
                    std::string g2 = "!(" + sl.arst_expr + ")";
                    cond = cond.empty() ? g2 : (g2 + " && " + cond);
                }
                if (is_wide(reg.width)) {
                    if (!cond.empty()) fprintf(out, "    if (%s) {\n", cond.c_str());
                    if (is_wide(sl.width)) {
                        int nw = nlimbs(sl.width);
                        fprintf(out, "      { %s _wsl[%d];\n", lt(), nw);
                        emit_materialize(out, "_wsl", nw, sl.d_sig, sigmap, false, 0);
                        fprintf(out, "      wplacew(%s, %d, _wsl, 0, %d); }\n",
                                dst.c_str(), sl.offset, sl.width);
                    } else
                        fprintf(out, "      wplacew_s(%s, %d, (uint64_t)(%s), 0, %d);\n",
                                dst.c_str(), sl.offset, sl.d_expr.c_str(), sl.width);
                    if (!cond.empty()) fprintf(out, "    }\n");
                } else {
                    uint64_t m = (sl.width >= 64) ? ~0ull
                               : (((1ull << sl.width) - 1) << sl.offset);
                    if (!cond.empty())
                        fprintf(out, "    if (%s)\n    ", cond.c_str());
                    fprintf(out, "    %s = (%s & ~UINT64_C(0x%llx)) | "
                                 "(((uint64_t)(%s) << %d) & UINT64_C(0x%llx));\n",
                            dst.c_str(), dst.c_str(), (unsigned long long)m,
                            sl.d_expr.c_str(), sl.offset, (unsigned long long)m);
                }
            }
            continue;
        }
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
    //
    // ORDER: multiple write ports to one memory in the same cycle carry RTL
    // priority — proc_memwr numbers them by PORTID in statement order, later
    // statements winning byte overlaps. Iterating cells_ (name order) emitted
    // them arbitrarily: Vortex's AMO writeback queue does a shift (pop) and an
    // indexed push to the SAME slot in one clock, push written last in the RTL;
    // name order put the shift after the push, so the pushed entry was wiped
    // and one concurrent atomic update vanished (atomtest 2067/2080). Sort by
    // (MEMID, PORTID|PRIORITY) ascending so the last-in-RTL write lands last.
    {
        bool hdr = false;
        std::vector<RTLIL::Cell*> wcells;
        for (auto &c : mod->cells_) {
            auto *cell = c.second;
            auto wtype = cell->type.str();
            if (wtype != "$memwr" && wtype != "$memwr_v2") continue;
            wcells.push_back(cell);
        }
        auto wprio = [](RTLIL::Cell *cell) {
            if (cell->hasParam(ID(PORTID)))
                return cell->getParam(ID(PORTID)).as_int();
            if (cell->hasParam(ID(PRIORITY)))
                return cell->getParam(ID(PRIORITY)).as_int();
            return 0;
        };
        std::stable_sort(wcells.begin(), wcells.end(),
            [&](RTLIL::Cell *a, RTLIL::Cell *b) {
                auto ma = a->getParam(ID(MEMID)).decode_string();
                auto mb = b->getParam(ID(MEMID)).decode_string();
                if (ma != mb) return ma < mb;
                return wprio(a) < wprio(b);
            });
        for (auto *cell : wcells) {
            if (!hdr) { fprintf(out, "    // Memory write ports\n"); hdr = true; }
            std::string mn = cname(cell->getParam(ID(MEMID)).decode_string());
            int abits = cell->getParam(ID(ABITS)).as_int();
            uint64_t amask = (abits >= 64) ? ~0ULL : ((1ULL << abits) - 1);
            std::string addr = sig_expr(cell->getPort(ID(ADDR)), sigmap);
            {
                auto mit = memories.find(cell->getParam(ID(MEMID)).decode_string());
                int mw = (mit != memories.end()) ? mit->second.width : 64;
                if (mw > 64) {
                    // wide-word: materialize DATA/EN limbs, masked RMW per limb.
                    // EN gating: any nonzero EN bit writes its limbs; the whole
                    // statement is unguarded (per-bit EN handled by the mask),
                    // but skip work when EN is all-zero.
                    int wnl = nlimbs(mw);
                    if (!hdr) { fprintf(out, "    // Memory write ports\n"); hdr = true; }
                    std::string gd;
                    if (g_gated && g_gp.memreaders.count(mn))
                        gd = gd_or_str(g_gp.memreaders[mn]);
                    fprintf(out, "    { %s _md[%d], _me[%d];\n", lt(), wnl, wnl);
                    emit_materialize(out, "_md", wnl, cell->getPort(ID(DATA)), sigmap, false, 0);
                    emit_materialize(out, "_me", wnl, cell->getPort(ID(EN)), sigmap, false, 0);
                    if (masked)
                        fprintf(out, "      if ((posedge_mask & 1u) && wred_or(_me,%d)) {\n", wnl);
                    else
                        fprintf(out, "      if (wred_or(_me,%d)) {\n", wnl);
                    fprintf(out, "        uint64_t _wa = ((uint64_t)(%s) & UINT64_C(0x%llx)) * %d;\n",
                            addr.c_str(), (unsigned long long)amask, wnl);
                    fprintf(out, "        for (int _l = 0; _l < %d; _l++)\n"
                                 "          %s->%s[_wa+_l] = (%s->%s[_wa+_l] & ~_me[_l]) | (_md[_l] & _me[_l]);\n",
                            wnl, sp, mn.c_str(), sp, mn.c_str());
                    if (!gd.empty()) fprintf(out, "       %s\n", gd.c_str());
                    fprintf(out, "      } }\n");
                    continue;
                }
            }
            std::string data = sig_expr(cell->getPort(ID(DATA)), sigmap);
            std::string en   = sig_expr(cell->getPort(ID(EN)), sigmap);
            // Masked write: bits where EN=1 take DATA, EN=0 keep old word. Handles
            // uniform full-word enables (FIFOs) and partial/byte enables alike.
            // Memory writes are owned by the main clk group (bit 0) when masked.
            std::string menc = masked ? std::string("(posedge_mask & 1u) && ") + en : en;
            std::string gd;
            if (g_gated && g_gp.memreaders.count(mn))
                gd = gd_or_str(g_gp.memreaders[mn]);
            fprintf(out, "    if (%s) { uint64_t _wa = (%s) & UINT64_C(0x%llx); "
                         "%s->%s[_wa] = (%s->%s[_wa] & ~(uint64_t)(%s)) | ((%s) & (%s));%s }\n",
                    menc.c_str(), addr.c_str(), (unsigned long long)amask,
                    sp, mn.c_str(), sp, mn.c_str(), en.c_str(), data.c_str(), en.c_str(),
                    gd.c_str());
        }
    }
    fprintf(out, "\n");

    // FSM coverage tracking (owned by the main clk group when masked).
    // Compiled out of the hot commit path unless -DSM_FSM_COV: it updates
    // shared per-FSM tables every posedge and inflates sm_clock.
    if (!fsms.empty()) {
        fprintf(out, "#ifdef SM_FSM_COV\n");
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
        fprintf(out, "#endif // SM_FSM_COV\n");
        fprintf(out, "\n");
    }
    };  // end emit_seq lambda

    // GSM_GATED: post-seq register-commit dirtying.  Compares against a
    // persistent prev copy (NOT the pre-edge alias — async-reset overrides
    // mutate both alias and state and would mask the transition), so it is
    // valid after ANY commit path: plain, masked, or late.
    auto emit_reg_dirty = [&](const char *sp) {
        if (!g_gated) return;
        fprintf(out, "    // register-change dirtying\n");
        for (auto &reg : registers) {
            auto rit = g_gp.readers.find(reg.name);
            if (rit == g_gp.readers.end()) continue;
            if (is_wide(reg.width)) {
                int nl = nlimbs(reg.width);
                fprintf(out, "    if (_gd_wne(_pv%s, %s->%s, %d)) { "
                             "wcopy(_pv%s, %s->%s, %d);\n",
                        reg.name.c_str(), sp, reg.name.c_str(), nl,
                        reg.name.c_str(), sp, reg.name.c_str(), nl);
            } else
                fprintf(out, "    if (_pv%s != %s->%s) { _pv%s = %s->%s;\n",
                        reg.name.c_str(), sp, reg.name.c_str(),
                        reg.name.c_str(), sp, reg.name.c_str());
            gd_emit_or(out, rit->second, -1, "        ");
            fprintf(out, "    }\n");
        }
    };

    // Output copies (the sm_comb tail) — trace through sigmap to find the source.
    auto emit_outputs = [&]() {
    fprintf(out, "    // Outputs\n");
    int _oi = 0;
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_output) {
            std::string wn = cname(wire->name.str());
            SigSpec port_sig(wire);
            if (_oi < 256)
                fprintf(out, "    if (sm_live_outputs[%d] & (1ull<<%d)) {\n",
                        _oi >> 6, _oi & 63);
            if (is_wide(wire->width)) {
                std::string dst = "o->" + wn;
                int ny = nlimbs(wire->width);
                emit_materialize(out, dst, ny, port_sig, sigmap, false, 0);
                emit_wmask(out, dst, wire->width, ny);
            } else {
                std::string expr = sig_expr(port_sig, sigmap);
                fprintf(out, "    o->%s = %s;\n", wn.c_str(), expr.c_str());
            }
            if (_oi < 256) fprintf(out, "    }\n");
            _oi++;
        }
    }
    };  // end emit_outputs lambda

    // sm_comb: combinational OUTPUTS from the CURRENT register state + inputs,
    // with no side effects (no register/memory commit). The bridge re-runs this
    // on every boundary-input-change delta to settle combinational outputs.
    // sm_comb's only consumer-visible product is `o` (VERIFY compare, xcheck,
    // sm_eval and the bridge's settle evals all read outputs only; SMDUMP has
    // its own full-pass sm_dump_comb) — so evaluate ONLY the output cones:
    // for VeeR's whole-core chunk that is ~3%% of the network per settle eval.
    // FSM-SPEC only specializes the single-clock path (multi-clock uses masked
    // seq and is left general for now).
    bool fsm_spec = !g_spec_regname.empty() && extra_clocks.empty();
    if (fsm_spec) {
        for (int k = 0; k < g_spec_nstates; k++) {
            g_spec_value = k;
            fprintf(out, "static void sm_comb_%d(state_t *s, const inputs_t *in, outputs_t *o) {\n", k);
            emit_comb_prefix("s");
            emit_outcone_cells();
            emit_outputs();
            fprintf(out, "}\n");
        }
        g_spec_value = -1;
        fprintf(out, "void sm_comb(state_t *s, const inputs_t *in, outputs_t *o) {\n");
        fprintf(out, "    switch((unsigned)(s->%s) & %uu) {\n",
                g_spec_regname.c_str(), (unsigned)(g_spec_nstates - 1));
        for (int k = 0; k < g_spec_nstates; k++)
            fprintf(out, "    case %d: sm_comb_%d(s,in,o); return;\n", k, k);
        fprintf(out, "    }\n}\n\n");
    } else {
        fprintf(out, "void sm_comb(state_t *s, const inputs_t *in, outputs_t *o) {\n");
        if (g_gated) {
            // Same gated pass as sm_clock (shared _bd + boundary statics):
            // settle-loop re-evals only re-run input-dirtied blocks, and the
            // following sm_clock finds the comb already settled.
            g_emit_gated_body = true;
            emit_comb("s");
            g_emit_gated_body = false;
            emit_outputs();
        } else {
            emit_comb_prefix("s");
            fprintf(out, "    // output cones only\n");
            emit_outcone_cells();
            emit_outputs();
        }
        fprintf(out, "}\n\n");
    }

    // Debug: dump EVERY combinational wire + register (for a net-level differential
    // against a reference sim — pinpoints the deepest miscompiled net, not just a
    // divergent output). Guarded by SM_DUMP so production builds skip the bloat.
    fprintf(out, "#ifdef SM_DUMP\n");
    fprintf(out, "#include <stdio.h>\n");
    fprintf(out, "void sm_dump_comb(state_t *s, const inputs_t *in, FILE *_df) {\n");
    emit_comb("s");
    // Print each wire RESOLVED through sigmap/read-redirect (sig_expr /
    // emit_materialize), not its raw local: wires that are pure aliases of
    // register slices or other nets are never assigned by emit_comb (their
    // readers reference the source directly), so the raw local prints a dead
    // 0 — which made the dump lie beside live values (req_rem_mask_r=0 next
    // to batch_mask=2) and cost a debugging day on the Vortex multi-lane bug.
    for (auto &w : mod->wires_) {
        auto *wire = w.second;
        if (wire->port_input) continue;
        std::string wn = cname(wire->name.str());
        if (wn == "_clk" || wn == "_rst") continue;
        if (is_wide(wire->width)) {
            int nl = nlimbs(wire->width);
            fprintf(out, "    { %s _dw[%d];\n", lt(), nl);
            emit_materialize(out, "_dw", nl, RTLIL::SigSpec(wire), sigmap, false, 0);
            fprintf(out, "    fprintf(_df,\"%s=\");", wn.c_str());
            for (int l = nl - 1; l >= 0; l--)
                if (g_w64)
                    fprintf(out, " fprintf(_df,\"%%016llx\",(unsigned long long)_dw[%d]);", l);
                else
                    fprintf(out, " fprintf(_df,\"%%08x\",(unsigned)_dw[%d]);", l);
            fprintf(out, " fprintf(_df,\"\\n\"); }\n");
        } else
            fprintf(out, "    fprintf(_df,\"%s=%%llx\\n\",(unsigned long long)(%s));\n",
                    wn.c_str(), sig_expr(RTLIL::SigSpec(wire), sigmap).c_str());
    }
    fprintf(out, "}\n#endif\n\n");

    // sm_clock: advance the registers / memory to the next state (no outputs).
    // Single-clock designs: byte-identical to before (one unconditional group).
    // Multi-clock: sm_clock_masked advances only the groups whose clock posedged
    // (posedge_mask), reading the pre-edge snapshot `src` (so coincident edges and
    // the cross-delta clock race both sample one frozen pre-edge state).
    if (extra_clocks.empty()) {
        if (fsm_spec) {
            for (int k = 0; k < g_spec_nstates; k++) {
                g_spec_value = k;
                fprintf(out, "static void sm_clock_%d(state_t *s, const inputs_t *in) {\n", k);
                emit_comb("s");
                emit_seq("s", false);
                fprintf(out, "}\n");
            }
            g_spec_value = -1;
            fprintf(out, "void sm_clock(state_t *s, const inputs_t *in) {\n");
            fprintf(out, "    switch((unsigned)(s->%s) & %uu) {\n",
                    g_spec_regname.c_str(), (unsigned)(g_spec_nstates - 1));
            for (int k = 0; k < g_spec_nstates; k++)
                fprintf(out, "    case %d: sm_clock_%d(s,in); return;\n", k, k);
            fprintf(out, "    }\n}\n\n");
        } else if (g_comb_split > 1 && !g_gated && !g_census) {
            // ---- GSM_COMB_SPLIT: sm_clock as K part functions + a commit ----
            // Restores register-allocator locality on GPU: one 24k-cell comb
            // function spilled ~7x worse per eval than a half-size one.
            // Contiguous topo slices; wires produced in one part and consumed
            // in a later part (or by the register/memory commits) travel in an
            // xw_state_t struct.  Bit-identical by construction: same cells,
            // same order, same expressions — only the function boundaries and
            // the cross-wire round-trips through xw are new.
            const int K = g_comb_split;
            // part_of MUST use the exact emission ranges below — the closed
            // form (i*K)/N disagrees at boundaries (cell N*p/K classified
            // p-1), and ONE misclassified cell reads its inputs stale and
            // stores its output before computing it (the ifc-fsm bug).
            std::map<RTLIL::Cell*, int> part_of;
            for (int p = 0; p < K; p++) {
                size_t lo = (sorted.size() * (size_t)p) / K;
                size_t hi = (sorted.size() * (size_t)(p + 1)) / K;
                for (size_t i = lo; i < hi; i++) part_of[sorted[i]] = p;
            }
            auto res_wire = [&](RTLIL::SigBit bit) -> RTLIL::Wire* {
                RTLIL::SigBit b = sigmap(bit);
                if (g_redirect) {
                    auto it = g_redirect->find(b);
                    if (it != g_redirect->end()) b = it->second;
                }
                return b.wire;
            };
            struct XInfo {
                int minw = 1 << 30;          // first writer part
                std::set<int> wparts;        // all writer parts
                std::set<int> touch;         // parts reading or writing
                bool seq = false;            // read by register/memwr commits
            };
            std::map<RTLIL::Wire*, XInfo> xi;
            for (auto *cell : sorted) {
                int p = part_of.at(cell);
                bool ismemrd = cell->type.str().compare(0, 6, "$memrd") == 0;
                for (auto &conn : cell->connections()) {
                    bool is_out = (conn.first == ID::Y)
                                  || (ismemrd && conn.first == ID(DATA));
                    if (is_out) {
                        // writers keyed by the RAW driven wire (the name the
                        // emission assigns), matching wire_driver's keying
                        for (auto &chunk : conn.second.chunks()) {
                            if (!chunk.wire) continue;
                            auto &x = xi[chunk.wire];
                            if (p < x.minw) x.minw = p;
                            x.wparts.insert(p);
                            x.touch.insert(p);
                        }
                    } else {
                        for (auto &bit : conn.second) {
                            RTLIL::Wire *w = res_wire(bit);
                            if (w) xi[w].touch.insert(p);
                        }
                    }
                }
            }
            // commit-side reads: register D/EN/ARST/SRST + memory ADDR/DATA/EN
            for (auto &c2 : mod->cells_) {
                auto *cell = c2.second;
                auto t = cell->type.str();
                bool isdff = (t == "$adff" || t == "$dff" || t == "$adffe"
                              || t == "$dffe" || t == "$sdff" || t == "$sdffe");
                bool ismemwr = t.compare(0, 6, "$memwr") == 0;
                if (!isdff && !ismemwr) continue;
                for (auto &conn : cell->connections()) {
                    if (conn.first == ID::Q) continue;
                    for (auto &bit : conn.second) {
                        RTLIL::Wire *w = res_wire(bit);
                        if (!w) continue;
                        auto it = xi.find(w);
                        if (it != xi.end()) it->second.seq = true;
                    }
                }
            }
            if (const char *dbgw = getenv("GSM_COMB_SPLIT_DEBUG")) {
                for (auto &kv : xi) {
                    if (cname(kv.first->name.str()) != dbgw) continue;
                    auto &x = kv.second;
                    fprintf(stderr, "[split-dbg] %s minw=%d wparts={", dbgw, x.minw);
                    for (int w2 : x.wparts) fprintf(stderr, "%d,", w2);
                    fprintf(stderr, "} touch={");
                    for (int t2 : x.touch) fprintf(stderr, "%d,", t2);
                    fprintf(stderr, "} seq=%d\n", (int)x.seq);
                }
                // and every cell whose connections resolve to it
                for (auto *cell : sorted) {
                    for (auto &conn : cell->connections()) {
                        for (auto &bit : conn.second) {
                            RTLIL::Wire *w2 = res_wire(bit);
                            if (w2 && cname(w2->name.str()) == dbgw)
                                fprintf(stderr, "[split-dbg] cell %s type %s port %s part %d\n",
                                        cell->name.c_str(), cell->type.c_str(),
                                        conn.first.c_str(), part_of.at(cell));
                        }
                    }
                }
            }
            std::vector<RTLIL::Wire*> xwires;
            bool xall = getenv("GSM_COMB_SPLIT_ALL") != nullptr;  // bisect aid
            for (auto &kv : xi) {
                auto &x = kv.second;
                if (x.minw == (1 << 30)) continue;   // no comb writer: alias/reg/input
                int maxt = -1;
                for (int t2 : x.touch) if (t2 > maxt) maxt = t2;
                if (xall || x.seq || maxt > x.minw) {
                    if (xall) { x.seq = true; for (int p2 = 0; p2 < K; p2++) x.touch.insert(p2); }
                    xwires.push_back(kv.first);
                }
            }
            std::sort(xwires.begin(), xwires.end(),
                [](RTLIL::Wire *a, RTLIL::Wire *b) { return a->name.str() < b->name.str(); });
            fprintf(out, "// GSM_COMB_SPLIT=%d: %zu cross-part wires\n", K, xwires.size());
            fprintf(out, "typedef struct {\n");
            for (auto *w : xwires) {
                std::string wn = cname(w->name.str());
                if (is_wide(w->width))
                    fprintf(out, "    %s %s[%d];\n", lt(), wn.c_str(), nlimbs(w->width));
                else
                    fprintf(out, "    %s %s;\n", ctype(w->width), wn.c_str());
            }
            fprintf(out, "} xw_state_t;\n"
                         "#ifndef SM_XW_QUAL\n#define SM_XW_QUAL\n#endif\n"
                         "static SM_XW_QUAL xw_state_t sm_xw;\n\n");
            for (int p = 0; p < K; p++) {
                fprintf(out, "static void sm_clock_p%d(state_t *s, const inputs_t *in, xw_state_t *xw) {\n", p);
                emit_comb_prefix("s");
                fprintf(out, "    // cross-part loads\n");
                for (auto *w : xwires) {
                    auto &x = xi[w];
                    if (p > x.minw && x.touch.count(p)) {
                        std::string wn = cname(w->name.str());
                        if (is_wide(w->width))
                            fprintf(out, "    wcopy(%s, xw->%s, %d);\n",
                                    wn.c_str(), wn.c_str(), nlimbs(w->width));
                        else
                            fprintf(out, "    %s = xw->%s;\n", wn.c_str(), wn.c_str());
                    }
                }
                size_t lo = (sorted.size() * (size_t)p) / K;
                size_t hi = (sorted.size() * (size_t)(p + 1)) / K;
                fprintf(out, "    // cells %zu..%zu\n", lo, hi);
                for (size_t i = lo; i < hi; i++) emit_cell(sorted[i]);
                fprintf(out, "    // cross-part stores\n");
                for (auto *w : xwires) {
                    auto &x = xi[w];
                    if (x.wparts.count(p)) {
                        std::string wn = cname(w->name.str());
                        if (is_wide(w->width))
                            fprintf(out, "    wcopy(xw->%s, %s, %d);\n",
                                    wn.c_str(), wn.c_str(), nlimbs(w->width));
                        else
                            fprintf(out, "    xw->%s = %s;\n", wn.c_str(), wn.c_str());
                    }
                }
                fprintf(out, "}\n\n");
            }
            fprintf(out, "static void sm_clock_pc(state_t *s, const inputs_t *in, xw_state_t *xw) {\n");
            emit_comb_prefix("s");
            fprintf(out, "    // cross-part loads (commit reads)\n");
            for (auto *w : xwires) {
                if (!xi[w].seq) continue;
                std::string wn = cname(w->name.str());
                if (is_wide(w->width))
                    fprintf(out, "    wcopy(%s, xw->%s, %d);\n",
                            wn.c_str(), wn.c_str(), nlimbs(w->width));
                else
                    fprintf(out, "    %s = xw->%s;\n", wn.c_str(), wn.c_str());
            }
            emit_seq("s", false);
            fprintf(out, "}\n\n");
            fprintf(out, "void sm_clock(state_t *s, const inputs_t *in) {\n");
            for (int p = 0; p < K; p++)
                fprintf(out, "    sm_clock_p%d(s, in, &sm_xw);\n", p);
            fprintf(out, "    sm_clock_pc(s, in, &sm_xw);\n");
            fprintf(out, "}\n\n");
        } else {
            fprintf(out, "void sm_clock(state_t *s, const inputs_t *in) {\n");
            g_emit_gated_body = g_gated;
            emit_comb("s");
            emit_seq("s", false);
            g_emit_gated_body = false;
            emit_reg_dirty("s");
            fprintf(out, "}\n\n");
        }
        fprintf(out, "void sm_clock_out(state_t *s, const inputs_t *in, "
                     "outputs_t *o, unsigned posedge_mask) {\n");
        fprintf(out, "    (void)posedge_mask;\n");
        emit_comb("s");
        emit_seq("s", false);
        emit_reg_dirty("s");
        fprintf(out, "    // refresh committed registers read by the output cones\n");
        for (auto &rn : outcone_regs) {
            int w = regwidth.count(rn) ? regwidth[rn] : 1;
            if (is_wide(w))
                fprintf(out, "    wcopy(%s, s->%s, %d);\n",
                        rn.c_str(), rn.c_str(), nlimbs(w));
            else
                fprintf(out, "    %s = s->%s;\n", rn.c_str(), rn.c_str());
        }
        fprintf(out, "    // output-cone recompute (post-edge values)\n");
        emit_outcone_cells();
        emit_outputs();
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
        g_emit_gated_body = g_gated;
        emit_comb("s");
        g_emit_gated_body = false;
        emit_seq("s", true);
        emit_reg_dirty("s");
        fprintf(out, "}\n\n");
        fprintf(out, "void sm_clock(state_t *s, const inputs_t *in) {\n");
        fprintf(out, "    sm_clock_masked(s, in, ~0u);\n");
        fprintf(out, "}\n\n");
        fprintf(out, "void sm_clock_out(state_t *s, const inputs_t *in, "
                     "outputs_t *o, unsigned posedge_mask) {\n");
        fprintf(out, "    (void)posedge_mask;\n");
        emit_comb("s");
        emit_seq("s", true);
        emit_reg_dirty("s");
        fprintf(out, "    // refresh committed registers read by the output cones\n");
        for (auto &rn : outcone_regs) {
            int w = regwidth.count(rn) ? regwidth[rn] : 1;
            if (is_wide(w))
                fprintf(out, "    wcopy(%s, s->%s, %d);\n",
                        rn.c_str(), rn.c_str(), nlimbs(w));
            else
                fprintf(out, "    %s = s->%s;\n", rn.c_str(), rn.c_str());
        }
        fprintf(out, "    // output-cone recompute (post-edge values)\n");
        emit_outcone_cells();
        emit_outputs();
        fprintf(out, "}\n\n");

        // sm_clock_late: advance ONLY the masked (extra) clock groups, with the
        // combinational cone computed from a PRE-EDGE state snapshot `k` (taken
        // by the bridge at the main posedge, before group 0 advanced) and the
        // CURRENT boundary inputs. This is the interp-faithful semantics of a
        // derived gated clock: its rise arrives deltas after clk, when internal
        // comb nets still hold pre-edge values but input signals have their
        // at-that-delta values. Group-0 commits, memory writes and FSM coverage
        // are all (posedge_mask & 1u)-guarded, so a mask of extra bits only
        // touches the extra groups.
        fprintf(out, "void sm_clock_late(state_t *s, state_t *k, "
                     "const inputs_t *in, unsigned posedge_mask) {\n");
        emit_comb("k");
        emit_seq("s", true);
        emit_reg_dirty("s");
        fprintf(out, "}\n\n");

        // Fused late commit: only the late-groups' D/EN/ARST cone is
        // evaluated from the snapshot (a handful of cells when the gated
        // clocks are internalized), then the committed-state output cones
        // are recomputed — replacing sm_clock_late's full-network pass AND
        // the bridge's follow-up full sm_comb.
        fprintf(out, "void sm_clock_late_out(state_t *s, state_t *k, "
                     "const inputs_t *in, outputs_t *o, unsigned posedge_mask) {\n");
        emit_comb_prefix("k");
        fprintf(out, "    // late-group D/EN/ARST cone only\n");
        for (auto *cell : sorted)
            if (latecone.count(cell)) emit_cell(cell);
        emit_seq("s", true);
        emit_reg_dirty("s");
        fprintf(out, "    // refresh committed registers read by the output cones\n");
        for (auto &rn : outcone_regs) {
            int w = regwidth.count(rn) ? regwidth[rn] : 1;
            if (is_wide(w))
                fprintf(out, "    wcopy(%s, s->%s, %d);\n",
                        rn.c_str(), rn.c_str(), nlimbs(w));
            else
                fprintf(out, "    %s = s->%s;\n", rn.c_str(), rn.c_str());
        }
        fprintf(out, "    // output-cone recompute (post-commit values)\n");
        emit_outcone_cells();
        emit_outputs();
        fprintf(out, "}\n\n");
    }
    // Per-output cone class: an output whose combinational cone reaches a
    // boundary INPUT is Mealy — the bridge must deposit it in the ACTIVE
    // region (immediately), like interp comb propagation. An output whose
    // cone is register-only is a flop Q — the bridge may commit it in the
    // NBA region (NVC_ACCEL_NBA), giving interp-exact `<=` timing across
    // chunk boundaries. Backward taint: seed = input ports; propagate
    // through comb cells + connections to fixpoint; register Q wires
    // terminate (they are state, not flow). CONSERVATIVE: anything not
    // proven register-only is listed Mealy (Mealy keeps today's behaviour).
    {
        // BIT-level taint: packed structs (VeeR predict packets) mix flop
        // fields with comb fields in ONE wire — wire-level analysis smears
        // them and misclassifies pure flop slices (e.g. exu_i0_br_hist_e4)
        // as Mealy. Track SigBits; register Q bits terminate propagation.
        pool<RTLIL::SigBit> regq;
        for (auto &c2 : mod->cells_) {
            RTLIL::Cell *cell = c2.second;
            std::string ty = cell->type.str();
            if (ty == "$adff" || ty == "$dff" || ty == "$adffe"
                || ty == "$dffe" || ty == "$sdff" || ty == "$sdffe") {
                RTLIL::SigSpec qs = sigmap(cell->getPort(ID::Q));
                for (auto &b : qs) if (b.wire) regq.insert(b);
            }
        }
        pool<RTLIL::SigBit> taint;
        auto bit_tainted = [&](const RTLIL::SigBit &mb) {
            return mb.wire && (mb.wire->port_input || taint.count(mb));
        };
        auto sig_tainted = [&](const RTLIL::SigSpec &sig) {
            RTLIL::SigSpec ms = sigmap(sig);
            for (auto &b : ms) if (bit_tainted(b)) return true;
            return false;
        };
        bool changed = true;
        while (changed) {
            changed = false;
            // cell granularity: any tainted input bit taints every output bit
            // (conservative for muxes/arith — correct)
            for (auto *cell : sorted) {
                bool t = false;
                for (auto &conn : cell->connections())
                    if (!cell->output(conn.first) && sig_tainted(conn.second)) {
                        t = true; break;
                    }
                if (!t) continue;
                for (auto &conn : cell->connections())
                    if (cell->output(conn.first)) {
                        RTLIL::SigSpec os = sigmap(conn.second);
                        for (auto &b : os)
                            if (b.wire && !regq.count(b) && taint.insert(b).second)
                                changed = true;
                    }
            }
            // connections: bit-precise (concat/slice plumbing of packed structs)
            for (auto &conn : mod->connections()) {
                RTLIL::SigSpec ls = sigmap(conn.first);
                RTLIL::SigSpec rs = sigmap(conn.second);
                const int n = std::min(ls.size(), rs.size());
                for (int i = 0; i < n; i++) {
                    RTLIL::SigBit lb = ls[i], rb = rs[i];
                    if (lb.wire && bit_tainted(rb) && !regq.count(lb)
                        && taint.insert(lb).second)
                        changed = true;
                }
            }
        }
        // Two bridge classes:
        //   DIRECT-Q output (every bit is literally a register Q bit): the
        //   interpreter updates it in the NBA region of the edge delta
        //   (visible d1) -> the bridge may sched_deposit(nonblock) it.
        //   EVERYTHING ELSE (comb of inputs and/or registers): the
        //   interpreter exposes it only after the post-edge comb cascade
        //   (d2+). Publishing it at d1 runs a delta ahead of interp and
        //   feeds level-sensitive clock-gater latches a cycle early (the
        //   dec+ifu NBA regression) -> the bridge stages it two deltas.
        fprintf(out, "const char *sm_comb_outputs[] = {");
        for (auto &w2 : mod->wires_) {
            auto *wire = w2.second;
            if (!wire->port_output) continue;
            RTLIL::SigSpec ms = sigmap(RTLIL::SigSpec(wire));
            bool direct_q = true;
            for (auto &b : ms)
                if (b.wire != nullptr && !regq.count(b)) { direct_q = false; break; }
            if (direct_q) continue;   // pure flop Q (constants allowed)
            std::string nm = cname(wire->name.str());
            if (!nm.empty() && nm[0] == '_') nm = nm.substr(1);
            fprintf(out, "\"%s\", ", nm.c_str());
            g_comb_out_names.push_back(nm);   // reused verbatim by the CXXRTL adapter
        }
        fprintf(out, "0};\n\n");
    }

    // Cross-file table: the bridge text-scrapes these to discover the extra clock
    // INPUT field base-names (matching pins[].name / `in._<name>`) and the count,
    // for per-clock edge detection. Always emitted so the symbols resolve.
    fprintf(out, "const char *sm_output_order[] = {");
    for (auto *ow : out_order) {
        std::string nm = cname(ow->name.str());
        if (!nm.empty() && nm[0] == '_') nm = nm.substr(1);
        fprintf(out, "\"%s\", ", nm.c_str());
    }
    fprintf(out, "0};\n\n");

    fprintf(out, "const char *sm_extra_clocks[] = {");
    for (auto &c : extra_clocks) {
        std::string nm = (!c.empty() && c[0] == '_') ? c.substr(1) : c;
        fprintf(out, "\"%s\", ", nm.c_str());
    }
    fprintf(out, "0};\n");
    fprintf(out, "#define SM_NUM_EXTRA_CLOCKS %zu\n", extra_clocks.size());
    // How many registers the MAIN clock (group 0) actually clocks.  A model
    // with SM_GROUP0_REGS 0 but extra clocks is clocked ENTIRELY by extras
    // (e.g. FPGA-shape rvdff members whose `clk` pin carries a dead gated
    // net while the flops run on rawclk) — the caller must then key its
    // edge arming / staged-output phases on a LIVE clock, not the main pin.
    {
        size_t g0 = 0;
        for (auto &reg : registers)
            if (reg.clk_group == 0) g0++;
        fprintf(out, "#define SM_GROUP0_REGS %zu\n\n", g0);
    }

    // sm_eval: back-compat wrapper — combinational outputs from the current state,
    // then commit (identical to the old single-function semantics).
    fprintf(out, "void sm_eval(state_t *s, const inputs_t *in, outputs_t *o) {\n");
    fprintf(out, "    sm_comb(s, in, o);\n");
    fprintf(out, "    sm_clock(s, in);\n");
    fprintf(out, "}\n\n");

    // FSM coverage report function
    if (!fsms.empty()) {
        fprintf(out, "#ifdef SM_FSM_COV\n");
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
        fprintf(out, "}\n");
        fprintf(out, "#endif // SM_FSM_COV\n\n");
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
    if (!fsms.empty()) {
        fprintf(out, "#ifdef SM_FSM_COV\n");
        fprintf(out, "    sm_fsm_coverage_report(stdout);\n");
        fprintf(out, "#endif\n");
    }
    fprintf(out, "    return 0;\n");
    fprintf(out, "}\n");
    fprintf(out, "#endif // SM_NO_MAIN\n");

    fclose(out);
    fprintf(stderr, "Generated %s: %zu comb cells, %zu registers\n",
            model_file.c_str(), sorted.size(), registers.size());

    // --- Generate NVC-mapped version ---
    // Wraps the standalone sm_eval with signal bridge code.
    // Compiled as .so, loaded by cycle_sim plugin.
    if (g_w64) {
        fprintf(stderr, "GSM_WIDE64: skipping the _nvc bridge companion "
                "(bridge ABI is 32-bit limbs)\n");
        return 0;
    }
    std::string mapped_file = model_file;
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
    fprintf(mout, "#include \"%s\"\n\n", model_file.c_str());

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

    // =======================================================================
    // VALUE-PLANE ADAPTER (GSM_CXXRTL=1)
    //
    // Emit `output_file` -- the model nvc's bridge #includes -- as a thin C++
    // adapter over the CXXRTL object written above.  CXXRTL's source is never
    // rewritten, only #included.  The adapter supplies exactly what nvc's
    // model-side contract needs:
    //   * inputs_t / state_t / outputs_t laid out EXACTLY as the C emitter
    //     lays them out (the bridge text-scrapes state_t for the demote
    //     writeback table and probes `uint64_t _<pin>;` per boundary pin);
    //   * sm_reset / sm_comb / sm_clock / sm_clock_out with the same
    //     no-register-advance / advance-and-recompute split;
    //   * sm_comb_outputs / sm_output_order / sm_extra_clocks verbatim.
    // Registers are MIRRORED from CXXRTL's .curr into state_t so nvc's
    // existing aj_chunk_demote writeback keeps working unchanged.
    //
    // The whole .so must then be compiled as C++:
    //     NVC_ACCEL_CC="g++ -x c++ -fpermissive -w -O2"
    // (-fpermissive because nvc's generated bridge is C and assigns void* to
    // typed pointers).  The extern "C" block below re-declares every symbol
    // the bridge defines AFTER this file is included, so they keep C linkage
    // and nvc's dlsym still finds them.
    // =======================================================================
    if (cxx_mode) {
        std::string decline;

        // ---- read back the generated header and index its members ----------
        std::map<std::string, CxxMember> members;
        std::string cxx_class;
        {
            FILE *hf = fopen(cxx_h.c_str(), "r");
            if (hf == NULL)
                decline = "cannot read generated header " + cxx_h;
            else {
                char ln[8192];
                while (fgets(ln, sizeof ln, hf)) {
                    std::string l(ln);
                    if (cxx_class.empty()) {
                        size_t sp = l.find("struct p_");
                        if (sp != std::string::npos
                            && l.find(": public module") != std::string::npos) {
                            size_t b = sp + 7, e = b;
                            while (e < l.size() && (isalnum((unsigned char)l[e]) || l[e] == '_')) e++;
                            cxx_class = l.substr(b, e - b);
                        }
                    }
                    // "<kind><W> <name>;"  with kind in {value, wire}
                    for (const char *kind : { "value<", "wire<" }) {
                        size_t k = l.find(kind);
                        if (k == std::string::npos) continue;
                        size_t wb = k + strlen(kind);
                        size_t we = l.find('>', wb);
                        if (we == std::string::npos) continue;
                        size_t nb = l.find_first_not_of(" \t", we + 1);
                        if (nb == std::string::npos) continue;
                        size_t ne = nb;
                        while (ne < l.size() && (isalnum((unsigned char)l[ne]) || l[ne] == '_')) ne++;
                        if (ne == nb || ne >= l.size() || l[ne] != ';') continue;
                        CxxMember m;
                        m.kind  = std::string(kind).substr(0, strlen(kind) - 1);
                        m.width = atoi(l.substr(wb, we - wb).c_str());
                        members[l.substr(nb, ne - nb)] = m;
                        break;
                    }
                }
                fclose(hf);
                if (cxx_class.empty())
                    decline = "no `struct p_... : public module` in " + cxx_h;
            }
        }

        // ---- structural preconditions for this (deliberately simple) cut ----
        if (decline.empty() && !extra_clocks.empty())
            decline = "multi-clock design (" + std::to_string(extra_clocks.size())
                    + " extra clock group(s)): the bridge drives sm_clock_masked/"
                      "sm_clock_late, which the value-plane adapter does not yet emit";
        if (decline.empty() && !memories.empty())
            decline = "design has " + std::to_string(memories.size())
                    + " memory(ies): the state_t memory mirror is not implemented";

        // clock/reset ports.  gen_statemachine keys the main flop group on the
        // wire whose cname is "_clk" and keeps it out of inputs_t; nvc likewise
        // strips a pin literally named clk/rst.  So the adapter has to drive the
        // CXXRTL clock port itself.
        RTLIL::Wire *clk_wire = NULL;
        bool has_rst_port = false;
        for (auto &w : mod->wires_) {
            const std::string cn = cname(w.second->name.str());
            if (cn == "_clk") clk_wire = w.second;
            else if (cn == "_rst" && w.second->port_input) has_rst_port = true;
        }
        if (decline.empty() && has_rst_port)
            decline = "module has a port literally named `rst`: the bridge drives "
                      "reset out of band (AJB[5]) and the adapter does not model it";
        if (decline.empty() && (clk_wire == NULL || !clk_wire->port_input))
            decline = "no boundary input wire whose cname is `_clk` -- the adapter "
                      "cannot synthesise the CXXRTL clock edge";

        std::string clk_mem, clk_prev;
        if (decline.empty()) {
            clk_mem  = cxx_mangle(clk_wire->name.str());
            clk_prev = "prev_" + clk_mem;
            if (!members.count(clk_mem))
                decline = "CXXRTL has no member `" + clk_mem + "` for the clock port";
            else if (!members.count(clk_prev))
                decline = "CXXRTL has no `" + clk_prev + "` edge shadow (the clock "
                          "does not clock anything in the CXXRTL netlist)";
        }

        // Every register / boundary pin must exist as a CXXRTL member of the
        // SAME width, or the mirror and the marshalling would silently diverge.
        // (A register whose gen_statemachine name fell back to the CELL name on
        // a cname collision has no wire member -- caught right here.)
        std::map<std::string, std::string> reg_member;    // state_t field -> member
        if (decline.empty()) {
            for (auto &reg : registers) {
                const std::string rid = (!reg.raw_qname.empty() && reg.raw_qname[0] == '$')
                                      ? reg.raw_qname : ("\\" + reg.raw_qname);
                const std::string mm = cxx_mangle(rid);
                auto it = members.find(mm);
                if (it == members.end()) {
                    decline = "register `" + reg.name + "` has no CXXRTL member `"
                            + mm + "`";
                    break;
                }
                if (it->second.width != reg.width) {
                    decline = "register `" + reg.name + "` width " + std::to_string(reg.width)
                            + " vs CXXRTL " + std::to_string(it->second.width);
                    break;
                }
                reg_member[reg.name] = mm;
            }
        }
        std::map<std::string, std::string> port_member;   // cname -> member
        if (decline.empty()) {
            for (auto &w : mod->wires_) {
                RTLIL::Wire *wire = w.second;
                if (!wire->port_input && !wire->port_output) continue;
                const std::string cn = cname(wire->name.str());
                if (cn == "_clk" || cn == "_rst") continue;
                const std::string mm = cxx_mangle(wire->name.str());
                auto it = members.find(mm);
                if (it == members.end() || it->second.width != wire->width) {
                    decline = "port `" + cn + "` has no matching CXXRTL member `"
                            + mm + "`";
                    break;
                }
                port_member[cn] = mm;
            }
        }

        if (!decline.empty()) {
            // Fall back to the hand-written C engine: it is the reference and
            // the fallback, so put it back where nvc expects the model.
            fprintf(stderr, "gen_statemachine: value-plane DECLINED -- %s\n",
                    decline.c_str());
            remove(output_file);
            if (rename(model_file.c_str(), output_file) != 0) {
                fprintf(stderr, "gen_statemachine: could not restore C model to %s\n",
                        output_file);
                Yosys::yosys_shutdown();
                return 1;
            }
            fprintf(stderr, "gen_statemachine: using the C engine for this chunk\n");
        }
        else {
        // ---- lvalue / rvalue helpers for a CXXRTL member -------------------
        // value<N> is unbuffered (read and write it directly); wire<N> is
        // double-buffered.  A register is read from .curr; an externally
        // written buffered signal must have BOTH halves written or the stale
        // .next is latched back over it by the next commit (the promote trap).
        auto rd = [&](const std::string &m) {
            return members[m].kind == "wire" ? ("d->" + m + ".curr") : ("d->" + m);
        };

        FILE *a = fopen(output_file, "w");
        if (a == NULL) {
            fprintf(stderr, "gen_statemachine: cannot write %s\n", output_file);
            Yosys::yosys_shutdown();
            return 1;
        }
        // CXXRTL's generated header opens with `#include <cxxrtl/cxxrtl.h>`, so
        // the runtime include dir has to reach the compiler on the command line
        // (the .so is built by nvc, from NVC_ACCEL_CC).  Resolve it here and
        // spell out the exact setting rather than making the caller guess.
        std::string rt_inc;
        {
            const char *env = getenv("GSM_CXXRTL_RUNTIME");
            if (env != NULL) rt_inc = env;
            else {
                // library-mode yosys cannot auto-locate share/, and
                // proc_share_dirname() log_error()s when it fails -- so probe
                // the known roots directly (same convention as the scan-JSON
                // techmap path below).
                const char *sd = getenv("YOSYS_SHARE");
                std::string share = sd ? std::string(sd)
                                       : (Yosys::yosys_share_dirname.empty()
                                          ? std::string("/usr/local/src/yosys-build/share/")
                                          : Yosys::yosys_share_dirname);
                if (!share.empty() && share.back() != '/') share += '/';
                const std::string cand[] = {
                    share + "include/backends/cxxrtl/runtime",
                    "/usr/local/src/yosys-build/share/include/backends/cxxrtl/runtime",
                    "/usr/local/src/yosys/backends/cxxrtl/runtime"
                };
                for (auto &c : cand) {
                    std::string probe = c + "/cxxrtl/cxxrtl.h";
                    if (FILE *pf = fopen(probe.c_str(), "r")) { fclose(pf); rt_inc = c; break; }
                }
            }
        }
        const std::string cc_line = "g++ -x c++ -fpermissive -w -O2 -I" + rt_inc;
        fprintf(a, "// VALUE-PLANE model: nvc chunk ABI over yosys CXXRTL.\n"
                   "// Generated by gen_statemachine (GSM_CXXRTL=1) from %s\n"
                   "// CXXRTL engine: %s (class cxxrtl_design::%s)\n"
                   "// Reference/fallback C engine: %s\n"
                   "// COMPILE AS C++:  NVC_ACCEL_CC=\"%s\"\n\n",
                inputs[0].c_str(), cxx_cc.c_str(), cxx_class.c_str(),
                model_file.c_str(), cc_line.c_str());
        fprintf(a, "#ifndef __cplusplus\n"
                   "#error \"value-plane model needs a C++ compiler: set "
                   "NVC_ACCEL_CC='g++ -x c++ -fpermissive -w -O2'\"\n#endif\n");
        fprintf(a, "#include <stdint.h>\n#include <stdio.h>\n#include <string.h>\n"
                   "#include <stddef.h>\n#include <new>\n\n");
        // Keep C linkage for every symbol nvc's bridge defines BELOW this
        // include -- in C++ they would otherwise be mangled (functions) or get
        // internal linkage (the const demote tables) and dlsym would miss them.
        fprintf(a, "extern \"C\" {\n"
                   "extern const char *aj_reg_name[];\n"
                   "extern const unsigned long aj_reg_off[];\n"
                   "extern const int aj_reg_width[];\n"
                   "extern const int aj_reg_depth[];\n"
                   "extern int aj_n_regs;\n"
                   "unsigned long aj_demote_state_off(void);\n"
                   "unsigned long accel_state_size(void);\n"
                   "void accel_reset(void *);\n"
                   "void accel_eval(void *, void **);\n"
                   "void *accel_in_addr(void *, int, unsigned long *);\n"
                   "void accel_dump(void *);\n"
                   "}\n\n");
        fprintf(a, "#include \"%s\"\n\n", cxx_cc.c_str());
        fprintf(a, "typedef cxxrtl_design::%s vp_design_t;\n\n", cxx_class.c_str());
        // Same sizing as the C emitter (the bridge negotiates the copy via
        // sm_live_outputs_words; both engines must agree).
        fprintf(a, "extern \"C\" const int sm_live_outputs_words = %d;\n",
                lo_words);
        fprintf(a, "extern \"C\" uint64_t sm_live_outputs[%d] = {", lo_words);
        for (int lw = 0; lw < lo_words; lw++)
            fprintf(a, "%s~0ull", lw ? "," : "");
        fprintf(a, "};\n\n");

        // ---- inputs_t : byte-identical to the C emitter ---------------------
        fprintf(a, "typedef struct {\n");
        bool has_in = false;
        for (auto &w : mod->wires_) {
            RTLIL::Wire *wire = w.second;
            if (!wire->port_input) continue;
            const std::string wn = cname(wire->name.str());
            if (wn == "_clk" || wn == "_rst") continue;
            if (is_wide(wire->width))
                fprintf(a, "    uint32_t %s[%d];  // %d bits\n",
                        wn.c_str(), nlimbs(wire->width), wire->width);
            else
                fprintf(a, "    %s %s;  // %d bits\n",
                        ctype(wire->width), wn.c_str(), wire->width);
            has_in = true;
        }
        if (!has_in) fprintf(a, "    int _dummy;\n");
        fprintf(a, "} inputs_t;\n\n");

        // ---- state_t : the register MIRROR + the CXXRTL object -------------
        // nvc scrapes this struct for the demote writeback table, matching only
        // `uint64_t <n>;  // <W> bits` / `uint32_t <n>[NL];  // <W> bits` lines,
        // so the raw design storage below is skipped by construction.
        fprintf(a, "typedef struct {\n");
        for (auto &reg : registers) {
            if (is_wide(reg.width))
                fprintf(a, "    uint32_t %s[%d];  // %d bits\n",
                        reg.name.c_str(), nlimbs(reg.width), reg.width);
            else
                fprintf(a, "    %s %s;  // %d bits\n",
                        ctype(reg.width), reg.name.c_str(), reg.width);
        }
        fprintf(a, "    // --- value-plane engine storage (NOT a register: the\n"
                   "    //     state_t scraper only matches uint64_t/uint32_t) ---\n");
        fprintf(a, "    unsigned char vp_raw[sizeof(vp_design_t)]"
                   " __attribute__((aligned(16)));\n");
        fprintf(a, "    unsigned char vp_live;\n");
        fprintf(a, "} state_t;\n\n");

        // ---- outputs_t : byte-identical to the C emitter ---------------------
        fprintf(a, "typedef struct {\n");
        for (auto &w : mod->wires_) {
            RTLIL::Wire *wire = w.second;
            if (!wire->port_output) continue;
            const std::string wn = cname(wire->name.str());
            if (is_wide(wire->width))
                fprintf(a, "    uint32_t %s[%d];  // %d bits\n",
                        wn.c_str(), nlimbs(wire->width), wire->width);
            else
                fprintf(a, "    %s %s;  // %d bits\n",
                        ctype(wire->width), wn.c_str(), wire->width);
        }
        fprintf(a, "} outputs_t;\n\n");

        fprintf(a, "static inline vp_design_t *vp_d(state_t *s)"
                   " { return (vp_design_t *)(void *)s->vp_raw; }\n\n");

        // ---- boundary marshalling ------------------------------------------
        // nvc packs a vector so that packed bit i == RTL bit i (its logic3d
        // element 0 is the MSB and the bridge shifts by W-1-b), and CXXRTL packs
        // value<N>::data[b>>5] bit (b&31).  Both are therefore plain LSB-first
        // little-endian bit planes: narrow fields are a shift/mask, and a wide
        // (>64b) field is a straight limb-for-limb copy.  Bits above W in the
        // top chunk MUST be left zero (CXXRTL's msb_mask invariant).
        fprintf(a, "static inline void vp_put_inputs(vp_design_t *d, const inputs_t *in) {\n");
        for (auto &w : mod->wires_) {
            RTLIL::Wire *wire = w.second;
            if (!wire->port_input) continue;
            const std::string wn = cname(wire->name.str());
            if (wn == "_clk" || wn == "_rst") continue;
            const std::string mm = port_member[wn];
            const int wd = wire->width;
            const bool buf = members[mm].kind == "wire";
            const char *suf1 = buf ? ".curr" : "";
            const char *suf2 = buf ? ".next" : "";
            for (int l = 0; l < nlimbs(wd); l++) {
                const int top = (wd - l * 32 >= 32) ? 32 : (wd - l * 32);
                char msk[64];
                msk[0] = '\0';
                if (top < 32)
                    snprintf(msk, sizeof msk, " & 0x%xu", (1u << top) - 1u);
                char src[256];
                if (is_wide(wd))
                    snprintf(src, sizeof src, "in->%s[%d]", wn.c_str(), l);
                else if (l == 0)
                    snprintf(src, sizeof src, "(uint32_t)(in->%s)", wn.c_str());
                else
                    snprintf(src, sizeof src, "(uint32_t)((in->%s) >> %d)",
                             wn.c_str(), l * 32);
                fprintf(a, "    d->%s%s.data[%d] = %s%s;\n", mm.c_str(), suf1, l, src, msk);
                if (buf)
                    fprintf(a, "    d->%s%s.data[%d] = d->%s%s.data[%d];\n",
                            mm.c_str(), suf2, l, mm.c_str(), suf1, l);
            }
        }
        fprintf(a, "}\n\n");

        fprintf(a, "static inline void vp_get_outputs(vp_design_t *d, outputs_t *o) {\n");
        for (auto &w : mod->wires_) {
            RTLIL::Wire *wire = w.second;
            if (!wire->port_output) continue;
            const std::string wn = cname(wire->name.str());
            const std::string mm = port_member[wn];
            const int wd = wire->width;
            const std::string r = rd(mm);
            if (is_wide(wd)) {
                for (int l = 0; l < nlimbs(wd); l++)
                    fprintf(a, "    o->%s[%d] = %s.data[%d];\n",
                            wn.c_str(), l, r.c_str(), l);
            }
            else if (wd > 32)
                fprintf(a, "    o->%s = (uint64_t)%s.data[0]"
                           " | ((uint64_t)%s.data[1] << 32);\n",
                        wn.c_str(), r.c_str(), r.c_str());
            else
                fprintf(a, "    o->%s = (uint64_t)%s.data[0];\n", wn.c_str(), r.c_str());
        }
        fprintf(a, "}\n\n");

        // ---- register mirror ------------------------------------------------
        // nvc's aj_chunk_demote reads the registers straight out of state_t at
        // offsetof(state_t, <field>) to write them back into the interpreter's
        // signals, so the mirror must be live whenever a demote can happen --
        // i.e. after every eval that could have moved a register.
        fprintf(a, "static inline void vp_sync_regs(state_t *s, vp_design_t *d) {\n");
        for (auto &reg : registers) {
            const std::string r = rd(reg_member[reg.name]);
            if (is_wide(reg.width)) {
                for (int l = 0; l < nlimbs(reg.width); l++)
                    fprintf(a, "    s->%s[%d] = %s.data[%d];\n",
                            reg.name.c_str(), l, r.c_str(), l);
            }
            else if (reg.width > 32)
                fprintf(a, "    s->%s = (uint64_t)%s.data[0]"
                           " | ((uint64_t)%s.data[1] << 32);\n",
                        reg.name.c_str(), r.c_str(), r.c_str());
            else
                fprintf(a, "    s->%s = (uint64_t)%s.data[0];\n",
                        reg.name.c_str(), r.c_str());
        }
        fprintf(a, "}\n\n");

        // A register can move OUTSIDE a clock edge only through an async reset
        // (CXXRTL emits `if (rst) q.next = ...` after the posedge block, so it
        // fires on any eval).  With no async reset in the design, sm_comb cannot
        // change state and the mirror refresh there is pure overhead.
        bool any_arst = false;
        for (auto &reg : registers) {
            if (!reg.arst_expr.empty()) any_arst = true;
            for (auto &sl : reg.slices) if (sl.has_arst) any_arst = true;
        }
        fprintf(a, "// async reset present: %s\n", any_arst ? "yes" : "no");

        // ---- the nvc model-side entry points --------------------------------
        // CXXRTL detects its own clock edges from prev_p_<clk>, which commit()
        // refreshes -- so instead of a posedge_mask the adapter simply presents
        // the clock level the bridge says this eval has, with the shadow forced
        // low so the edge is exactly one-shot and history-independent.
        fprintf(a, "extern \"C\" void sm_reset(state_t *s) {\n");
        fprintf(a, "    vp_design_t *d;\n");
        fprintf(a, "    if (!s->vp_live) { d = new (s->vp_raw) vp_design_t(); s->vp_live = 1; }\n");
        fprintf(a, "    else { d = vp_d(s); d->reset(); }\n");
        fprintf(a, "    vp_sync_regs(s, d);\n");
        fprintf(a, "}\n\n");

        // SETTLE.  step() is eval();commit(); repeated to a fixpoint.  With no
        // clock edge in play, commit() can only move something if the design has
        // buffered comb wires or feedback arcs -- and that is exactly what
        // eval()'s `converged` return reports.  So a converging design settles
        // in one eval() and pays no commit sweep at all; a non-converging one
        // falls back to the full step() loop.  (An ASYNC reset is the one thing
        // that can move a register outside an edge: CXXRTL emits it as an
        // unconditional post-posedge override, so such designs must commit.)
        const char *settle = any_arst ? "d->step();"
                                      : "if (!d->eval()) d->step();";
        fprintf(a, "extern \"C\" void sm_comb(state_t *s, const inputs_t *in, outputs_t *o) {\n");
        fprintf(a, "    vp_design_t *d = vp_d(s);\n");
        fprintf(a, "    vp_put_inputs(d, in);\n");
        fprintf(a, "    d->%s.data[0] = 0u;  d->%s.data[0] = 0u;   // no edge\n",
                clk_prev.c_str(), clk_mem.c_str());
        fprintf(a, "    %s\n", settle);
        fprintf(a, "    vp_get_outputs(d, o);\n");
        if (any_arst)
            fprintf(a, "    vp_sync_regs(s, d);   // async reset can move state here\n");
        fprintf(a, "}\n\n");

        // sm_clock_out is the bridge's FUSED entry: advance the registers AND
        // hand back POST-edge outputs (that is what the C engine's
        // "refresh committed registers / output-cone recompute" tail does).
        // CXXRTL evaluates the output cone from the PRE-commit .curr, so the
        // advance needs a second settle after commit or every output lags the
        // registers by exactly one clock.  For a converging design that is one
        // extra eval(); for a non-converging one step() re-settles properly.
        // (The extra pass is skipped when no output cone reads a register --
        // then the pre- and post-edge outputs are identical by construction.)
        bool out_reads_reg = !outcone_regs.empty();
        fprintf(a, "extern \"C\" void sm_clock_out(state_t *s, const inputs_t *in,"
                   " outputs_t *o, unsigned posedge_mask) {\n");
        fprintf(a, "    (void)posedge_mask;\n");
        fprintf(a, "    vp_design_t *d = vp_d(s);\n");
        fprintf(a, "    vp_put_inputs(d, in);\n");
        fprintf(a, "    d->%s.data[0] = 0u;  d->%s.data[0] = 1u;   // synthesise posedge\n",
                clk_prev.c_str(), clk_mem.c_str());
        fprintf(a, "    d->step();                 // advance registers\n");
        if (out_reads_reg)
            fprintf(a, "    %s   // post-edge output cone (the clock shadow is now"
                       " high, so no second edge)\n", settle);
        fprintf(a, "    vp_get_outputs(d, o);\n");
        fprintf(a, "    vp_sync_regs(s, d);\n");
        fprintf(a, "}\n\n");

        fprintf(a, "extern \"C\" void sm_clock(state_t *s, const inputs_t *in) {\n");
        fprintf(a, "    outputs_t _o; sm_clock_out(s, in, &_o, 1u);\n");
        fprintf(a, "}\n\n");

        fprintf(a, "extern \"C\" void sm_eval(state_t *s, const inputs_t *in, outputs_t *o) {\n");
        fprintf(a, "    sm_comb(s, in, o); sm_clock(s, in);\n}\n\n");
        fprintf(a, "extern \"C\" void sm_dump_comb(state_t *s, const inputs_t *in, FILE *f) {\n"
                   "    (void)s; (void)in;\n"
                   "    fprintf(f, \"#AJSM value-plane engine: internal nets are not"
                   " named at -g0\\n\");\n}\n\n");

        // ---- cross-file tables the bridge text-scrapes ----------------------
        fprintf(a, "const char *sm_comb_outputs[] = {");
        for (auto &n : g_comb_out_names) fprintf(a, "\"%s\", ", n.c_str());
        fprintf(a, "0};\n");
        fprintf(a, "const char *sm_output_order[] = {");
        for (auto *ow : out_order) {
            std::string nm = cname(ow->name.str());
            if (!nm.empty() && nm[0] == '_') nm = nm.substr(1);
            fprintf(a, "\"%s\", ", nm.c_str());
        }
        fprintf(a, "0};\n");
        fprintf(a, "const char *sm_extra_clocks[] = {0};\n");
        fprintf(a, "#define SM_NUM_EXTRA_CLOCKS 0\n");
        fclose(a);
        fprintf(stderr, "Generated %s: VALUE-PLANE engine (CXXRTL class %s,"
                        " %zu registers mirrored, %zu comb cells in the C reference)\n",
                output_file, cxx_class.c_str(), registers.size(), sorted.size());
        if (rt_inc.empty())
            fprintf(stderr, "gen_statemachine: WARNING could not locate the CXXRTL "
                            "runtime headers (set GSM_CXXRTL_RUNTIME)\n");
        fprintf(stderr, "gen_statemachine: value-plane needs NVC_ACCEL_CC=\"%s\"\n",
                cc_line.c_str());
        }
    }

    // --- Optional: emit a full-scan ISCAS89-source JSON of THIS design -------
    // With GSM_SCAN_JSON set, dump the design (post dffunmap/opt_clean, the same
    // state the model was built from) as gate-primitive JSON for json2bench.py.
    // Because the model AND the bench then come from ONE gen_statemachine run,
    // the register/port net names are exactly the cname()s used for the
    // state_t/inputs_t/outputs_t fields — so the per-cone certificate maps every
    // net (no json2bench<->gen_statemachine provenance mismatch). json2bench
    // turns each DFF into a PPI/PPO; dffunmap already folded sync reset/enable
    // into D, and async resets remain $_DFF_*_ whose R json2bench ignores —
    // matching the model's per-cycle next-state (= D; async reset via sm_reset).
    if (g_icg2en_used && getenv("GSM_SCAN_JSON"))
        fprintf(stderr, "gen_statemachine: GSM_SCAN_JSON skipped — icg2en "
                "left $dffe cells the scan flow does not model\n");
    if (const char *scan_json = g_icg2en_used ? NULL : getenv("GSM_SCAN_JSON")) {
        std::string tn = mod->name.str();
        if (!tn.empty() && tn[0] == '\\') tn = tn.substr(1);
        fprintf(stderr, "Emitting full-scan JSON -> %s (top %s)\n",
                scan_json, tn.c_str());
        // techmap reads share/techmap.v; library-mode yosys can't auto-locate
        // share/, so point it at the build's share dir (env-overridable).
        if (Yosys::yosys_share_dirname.empty()) {
            const char *sd = getenv("YOSYS_SHARE");
            Yosys::yosys_share_dirname =
                sd ? std::string(sd) : "/usr/local/src/yosys-build/share/";
            if (!Yosys::yosys_share_dirname.empty() &&
                Yosys::yosys_share_dirname.back() != '/')
                Yosys::yosys_share_dirname += '/';
        }
        Yosys::run_pass("techmap");    // $add/$eq/... -> gate primitives
        Yosys::run_pass("simplemap");  // $dff/$mux/... -> $_DFF_*_/$_MUX_/...
        Yosys::run_pass("opt_clean");
        Yosys::run_pass(std::string("write_json ") + scan_json);
        // Register wire-name manifest: json2bench prefers these netnames when
        // claiming register bits, so each PPI/PPO is named after the SAME wire
        // whose cname() is this reg's state_t field (a register net has several
        // aliases — e.g. bus_intf.bus_buffer.obuf_merge vs ...obuf_mergeff.dffs.
        // dout_reg — and json2bench's first-come pick would otherwise diverge).
        std::string regnames = std::string(scan_json) + ".regnames";
        if (FILE *rf = fopen(regnames.c_str(), "w")) {
            for (auto &reg : registers)
                if (!reg.raw_qname.empty())
                    fprintf(rf, "%s\n", reg.raw_qname.c_str());
            fclose(rf);
            fprintf(stderr, "Wrote %zu register names -> %s\n",
                    registers.size(), regnames.c_str());
        }
        fprintf(stderr, "SCAN_JSON_TOP %s\n", tn.c_str());
    }

    Yosys::yosys_shutdown();
    return 0;
}
