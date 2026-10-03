/*
 * TEST ONLY.  A stand-in analog engine C interface for run_cside.py: built as
 * libvacaskcinterface.so (default) or libxycecinterface.so (-DSTUB_XYCE) it
 * has the six entry points nvc loads, and <prefix>cosim_abi() only when built
 * with -DSTUB_ABI=<n>.  Without the ABI symbol it plays an engine that
 * predates the finish protocol (nvc must refuse it); with it, $STUB_MODE
 * picks what simulateUntil does, to reach every branch of nvc's run loop
 * without a real engine:
 *   stall     returns success without progress      -> "co-simulation stalled at 0 s"
 *   fail      returns failure at 50 ns, not complete -> "<engine> transient failed at 5e-08 s"
 *   complete  reaches 30 ns and reports completion   -> "co-simulation finished: analog end at 3e-08 s"
 *   sigint_init  raises SIGINT in initialize (an interrupt during engine
 *             initialisation)                       -> "co-simulation interrupted at 0 s"
 *   sigint_run   raises SIGINT in simulateUntil, then returns success at
 *             40 ns, not complete (an interrupt during the run)
 *                                                   -> "co-simulation interrupted at 0 s"
 *
 *   cc -O2 -shared -fPIC -o <dir>/libvacaskcinterface.so stub_engine.c [-DSTUB_ABI=2]
 *   cc -O2 -shared -fPIC -DSTUB_XYCE -o <dir>/libxycecinterface.so stub_engine.c [-DSTUB_ABI=2]
 */

#include <signal.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#ifdef STUB_XYCE
#define FN(name) xyce_##name
typedef bool complete_t;
#else
#define FN(name) vacask_##name
typedef int complete_t;
#endif

static int session;
static double reached;
static int complete;

static const char *mode(void)
{
   const char *m = getenv("STUB_MODE");
   return m != NULL ? m : "complete";
}

void FN(open)(void **ptr)
{
   *ptr = &session;
}

int FN(initialize)(void **ptr, int argc, char **argv)
{
   (void)ptr; (void)argc; (void)argv;
   reached = 0.0;
   complete = 0;
   if (strcmp(mode(), "sigint_init") == 0)
      raise(SIGINT);
   return 1;
}

int FN(simulateUntil)(void **ptr, double t, double *actual)
{
   (void)ptr; (void)t;
   if (strcmp(mode(), "stall") == 0) {
      *actual = reached;
      return 1;
   }
   else if (strcmp(mode(), "sigint_run") == 0) {
      raise(SIGINT);
      reached = 4e-8;
      *actual = reached;
      return 1;
   }
   else if (strcmp(mode(), "fail") == 0) {
      reached = 5e-8;
      *actual = reached;
      return 0;
   }
   else {
      reached = 3e-8;
      complete = 1;
      *actual = reached;
      return 1;
   }
}

complete_t FN(simulationComplete)(void **ptr)
{
   (void)ptr;
   return complete != 0;
}

double FN(getTime)(void **ptr)
{
   (void)ptr;
   return reached;
}

void FN(close)(void **ptr)
{
   *ptr = NULL;
}

#ifdef STUB_ABI
int FN(cosim_abi)(void)
{
   return STUB_ABI;
}
#endif
