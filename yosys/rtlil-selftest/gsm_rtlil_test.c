// Driver for the gsm_rtlil_* direct-construction facade: builds the rtoy
// design (see rtlil_toy.v) through the API and synthesizes it via the same
// pipeline as the text path.
// usage: gsm_rtlil_test <libgsm.so> <out.c> [label]
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

typedef int  (*begin_fn)(const char *);
typedef int  (*module_fn)(const char *);
typedef int  (*wire_fn)(const char *, int, int, const char *);
typedef int  (*connect_fn)(const char *, const char *);
typedef int  (*bin_fn)(const char *, const char *, const char *, const char *, const char *, int);
typedef int  (*mux_fn)(const char *, const char *, const char *, const char *, const char *);
typedef int  (*proc_fn)(const char *);
typedef int  (*sync_fn)(const char *, const char *);
typedef int  (*sassign_fn)(const char *, const char *);
typedef unsigned long long (*hash_fn)(void);
typedef int  (*synth_fn)(int, const char *const *);
typedef int  (*casgn_fn)(const char *, const char *);
typedef int  (*swb_fn)(const char *);
typedef int  (*caseb_fn)(const char *);
typedef int  (*end_fn)(void);

#define GET(v, n) v = (void *)dlsym(dl, n); \
   if (!v) { fprintf(stderr, "dlsym %s: %s\n", n, dlerror()); return 2; }
#define CK(call) do { int _rc = (call); if (_rc != 0) { \
   fprintf(stderr, "FAIL rc=%d: %s\n", _rc, #call); return 1; } } while (0)

int main(int argc, char **argv)
{
   if (argc < 3) { fprintf(stderr, "usage: %s <libgsm.so> <out.c> [label]\n", argv[0]); return 2; }
   void *dl = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
   if (!dl) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 2; }

   begin_fn   b_begin;   GET(b_begin,   "gsm_rtlil_begin");
   module_fn  b_module;  GET(b_module,  "gsm_rtlil_module");
   wire_fn    b_wire;    GET(b_wire,    "gsm_rtlil_wire");
   connect_fn b_conn;    GET(b_conn,    "gsm_rtlil_connect");
   bin_fn     b_bin;     GET(b_bin,     "gsm_rtlil_cell_bin");
   mux_fn     b_mux;     GET(b_mux,     "gsm_rtlil_cell_mux");
   proc_fn    b_proc;    GET(b_proc,    "gsm_rtlil_proc");
   sync_fn    b_sync;    GET(b_sync,    "gsm_rtlil_sync");
   sassign_fn b_sas;     GET(b_sas,     "gsm_rtlil_sync_assign");
   hash_fn    b_hash;    GET(b_hash,    "gsm_rtlil_content_hash");
   synth_fn   b_synth;   GET(b_synth,   "gsm_rtlil_synth");
   casgn_fn   b_casgn;   GET(b_casgn,   "gsm_rtlil_case_assign");
   swb_fn     b_swb;     GET(b_swb,     "gsm_rtlil_switch_begin");
   caseb_fn   b_caseb;   GET(b_caseb,   "gsm_rtlil_case_begin");
   end_fn     b_cend;    GET(b_cend,    "gsm_rtlil_case_end");
   end_fn     b_swe;     GET(b_swe,     "gsm_rtlil_switch_end");

   char out2[1024];
   snprintf(out2, sizeof out2, "%s.2", argv[2]);
   // TWO full sessions in ONE process: yosys's global autoidx advances
   // between them, so byte-equality of the two outputs proves the
   // canonicalization actually removes autoidx from the emitted C.
   for (int session = 0; session < 2; session++) {
   CK(b_begin(NULL));
   CK(b_module("rtoy"));
   CK(b_wire("clk",    1, 1, NULL));
   CK(b_wire("rst",    1, 1, NULL));
   CK(b_wire("d",      8, 1, NULL));
   CK(b_wire("q",      8, 2, NULL));
   CK(b_wire("y",      8, 2, NULL));
   CK(b_wire("a",      8, 0, "00000000"));
   CK(b_wire("b",      8, 0, "00000000"));
   CK(b_wire("c",      8, 0, "00000000"));
   CK(b_wire("en",     1, 0, NULL));
   CK(b_wire("t_add",  8, 0, NULL));
   CK(b_wire("a_next", 8, 0, NULL));
   CK(b_wire("t_xor",  8, 0, NULL));
   CK(b_wire("t_addc", 8, 0, NULL));
   CK(b_wire("t_and",  8, 0, NULL));
   CK(b_wire("g0_c",   8, 0, NULL));   /* decision-tree temp for c */

   CK(b_bin("add", "c_add", "d", "b", "t_add", 0));
   CK(b_mux("c_muxa", "t_add", "8'b00000000", "rst", "a_next"));
   CK(b_bin("xor", "c_xor", "a", "d", "t_xor", 0));
   CK(b_bin("add", "c_addc", "c", "a", "t_addc", 0));
   CK(b_bin("and", "c_and", "a", "b", "t_and", 0));
   CK(b_bin("xor", "c_y", "t_and", "c", "y", 0));
   CK(b_conn("en", "d[0]"));
   CK(b_conn("q", "a"));

   CK(b_proc("p_a"));
   CK(b_sync("posedge", "clk"));
   CK(b_sas("a", "a_next"));

   CK(b_proc("p_b"));
   CK(b_sync("posedge", "clk"));
   CK(b_sas("b", "t_xor"));
   CK(b_sync("level1", "rst"));
   CK(b_sas("b", "8'b00000000"));

   /* enable-gated register via the DECISION-TREE form (read_verilog's
      hold pattern): root action g0_c = c; switch(en) case 1: g0_c = t_addc;
      sync commits c <= g0_c — the untaken branch holds. */
   CK(b_proc("p_c"));
   CK(b_casgn("g0_c", "c"));
   CK(b_swb("en"));
   CK(b_caseb("1'b1"));
   CK(b_casgn("g0_c", "t_addc"));
   CK(b_cend());
   CK(b_swe());
   CK(b_sync("posedge", "clk"));
   CK(b_sas("c", "g0_c"));

   printf("content_hash=%016llx\n", b_hash());

   const char *label = (argc > 3) ? argv[3] : "rtlil-built:rtoy.v";
   const char *out = session == 0 ? argv[2] : out2;
   const char *args[3] = { label, "rtoy", out };
   int rc = b_synth(3, args);
   printf("synth rc=%d -> %s\n", rc, out);
   if (rc != 0)
      return rc;
   }
   return 0;
}
