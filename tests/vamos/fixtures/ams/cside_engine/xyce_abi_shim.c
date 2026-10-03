/*
 * TEST ONLY - never install.  Lets run_cside.py (--xyce-shim) exercise nvc's
 * Xyce co-simulation path against a Xyce C interface that predates the vamos
 * cosim patches (it has no xyce_cosim_abi).  Built as libxycecinterface.so in
 * a scratch directory placed first on LD_LIBRARY_PATH, it forwards the entry
 * points nvc uses to the real library named by $XYCE_SHIM_REAL and claims
 * co-simulation ABI 2.  Such an engine reads a finish (stepper result 2) as an
 * accepted step and keeps going with the digital frozen, so the runner skips
 * the cases that need the finish protocol when the shim is in use.
 *
 *   cc -O2 -shared -fPIC -o <dir>/libxycecinterface.so xyce_abi_shim.c -ldl
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>

static void *real_lib;

static void *real_sym(const char *name)
{
   if (real_lib == NULL) {
      const char *path = getenv("XYCE_SHIM_REAL");
      if (path == NULL || *path == '\0') {
         fprintf(stderr, "xyce_abi_shim: XYCE_SHIM_REAL is not set\n");
         exit(3);
      }
      real_lib = dlopen(path, RTLD_LAZY | RTLD_GLOBAL);
      if (real_lib == NULL) {
         fprintf(stderr, "xyce_abi_shim: %s\n", dlerror());
         exit(3);
      }
   }
   void *p = dlsym(real_lib, name);
   if (p == NULL) {
      fprintf(stderr, "xyce_abi_shim: %s has no %s\n",
              getenv("XYCE_SHIM_REAL"), name);
      exit(3);
   }
   return p;
}

int xyce_cosim_abi(void)
{
   return 2;
}

void xyce_open(void **ptr)
{
   ((void (*)(void **))real_sym("xyce_open"))(ptr);
}

int xyce_initialize(void **ptr, int argc, char **argv)
{
   return ((int (*)(void **, int, char **))real_sym("xyce_initialize"))(
      ptr, argc, argv);
}

int xyce_simulateUntil(void **ptr, double t, double *reached)
{
   return ((int (*)(void **, double, double *))real_sym("xyce_simulateUntil"))(
      ptr, t, reached);
}

void xyce_close(void **ptr)
{
   ((void (*)(void **))real_sym("xyce_close"))(ptr);
}

bool xyce_simulationComplete(void **ptr)
{
   return ((bool (*)(void **))real_sym("xyce_simulationComplete"))(ptr);
}

double xyce_getTime(void **ptr)
{
   return ((double (*)(void **))real_sym("xyce_getTime"))(ptr);
}
