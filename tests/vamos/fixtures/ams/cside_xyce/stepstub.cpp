// TEST ONLY - never install.  A code: URI library for standalone Xyce runs
// (no nvc) that answers Xyce's co-simulation candidate-step protocol
// (docs/VAMOS_AMS_DESIGN.md §7 P1; xyce/src/DeviceModelPKG/Core/
// N_DEV_SourceData.h): it lets run_cside_xyce.py test the Xyce side of the
// finish protocol and of BindCB on their own.
//
//   c++ -O2 -shared -fPIC -DSTUB_TAG=A -o libstepstub_A.so stepstub.cpp
//
// Deck use (a 0 A current source; the URI arguments are ignored):
//   Ia n 0 PWL FILE "code:/abs/libstepstub_A.so:stub_init:a2d:x"
// and, for BindCB's NULL case, the init function null_init.
//
// xyce_bridge_step answers by STEPSTUB_<TAG>_MODE:
//   accept     always 0
//   stop       nvc's stopped_step for a digital side stopped at
//              STEPSTUB_<TAG>_STOP seconds: accept before it, one veto to it
//              for the first step that ends past it (unless it lies at or
//              before the last accepted point), then finish (2)
//   vetofirst  the first step is vetoed to its midpoint (1, *tEvt = t/2),
//              every later step accepted
//   odd        the first step ending past STEPSTUB_<TAG>_AT is answered 3
//              with *tEvt = AT (a veto: any nonzero answer with *tEvt >= 0),
//              every later step 1 with *tEvt = -1 (an accept)
// Every call is appended to STEPSTUB_<TAG>_LOG as "<t> <answer> <tEvt>"
// (%.17g), so the test can see what Xyce offered and when it stopped.

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <utility>
#include <vector>

#ifndef STUB_TAG
#define STUB_TAG A
#endif
#define STR2(x) #x
#define STR(x) STR2(x)
#define TAG STR(STUB_TAG)

typedef std::vector<std::pair<double, double> > tTVVEC;
struct PWLinDynData;

// Indices into Xyce's function table (N_DEV_SourceDataExt.inc order)
enum { FN_GET_TVVEC = 0, FN_GET_TIME, FN_RESET_NUM };
typedef tTVVEC *(*fn_get_tvvec_t)(PWLinDynData *);
typedef void (*fn_reset_num_t)(PWLinDynData *);
enum { OP_Init = 0, OP_Update = 1 };

static const char *env(const char *key)
{
   char name[64];
   snprintf(name, sizeof(name), "STEPSTUB_%s_%s", TAG, key);
   const char *v = getenv(name);
   return v ? v : "";
}

static double env_time(const char *key)
{
   const char *v = env(key);
   return *v ? strtod(v, NULL) : 0.0;
}

// A 0 A source: the stub takes part in the step protocol only
static int source_cb(PWLinDynData *pwl, void *ext_data, int op, void *)
{
   if (op != OP_Update)
      return 0;
   void **fns = (void **)ext_data;
   tTVVEC *tv = ((fn_get_tvvec_t)fns[FN_GET_TVVEC])(pwl);
   tv->clear();
   tv->push_back(std::make_pair(0.0, 0.0));
   tv->push_back(std::make_pair(1e30, 0.0));
   ((fn_reset_num_t)fns[FN_RESET_NUM])(pwl);
   return 0;
}

extern "C" {

// *cb_data holds Xyce's function table on entry; it stays the callback data
void *stub_init(PWLinDynData *, void **, const char *)
{
   return (void *)source_cb;
}

// An init function that cannot bind its source and says so with NULL
void *null_init(PWLinDynData *, void **, const char *)
{
   return NULL;
}

int xyce_bridge_step(double t, double *t_evt)
{
   static bool vetoed = false, first = true;
   static double last_acc = 0.0;
   const char *mode = env("MODE");
   int r = 0;
   *t_evt = -1.0;

   if (strcmp(mode, "stop") == 0) {
      const double tf = env_time("STOP");
      const double tol = 1e-15 + 1e-14 * t;   // as nvc: 1 fs + 1e-14 relative
      if (t < tf - tol) {
         last_acc = t;
         r = 0;
      }
      else if (t > tf + tol && tf > last_acc + tol && !vetoed) {
         vetoed = true;
         *t_evt = tf;
         r = 1;
      }
      else {
         last_acc = t;
         r = 2;
      }
   }
   else if (strcmp(mode, "vetofirst") == 0) {
      if (first) {
         *t_evt = t / 2;
         r = 1;
      }
   }
   else if (strcmp(mode, "odd") == 0) {
      const double at = env_time("AT");
      if (!vetoed && t > at + 1e-15 + 1e-14 * at) {
         vetoed = true;
         *t_evt = at;
         r = 3;
      }
      else if (vetoed)
         r = 1;   // with *t_evt < 0: an accept
   }
   first = false;

   const char *log = env("LOG");
   if (*log) {
      FILE *f = fopen(log, "a");
      if (f) {
         fprintf(f, "%.17g %d %.17g\n", t, r, *t_evt);
         fclose(f);
      }
   }
   return r;
}

} // extern "C"
