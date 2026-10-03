/*
 * TEST ONLY.  Prints the co-simulation ABI a Xyce C interface reports, the way
 * nvc reads it (dlopen RTLD_LAZY|RTLD_GLOBAL, then dlsym xyce_cosim_abi):
 *
 *   cc -O2 -o abi_probe abi_probe.c -ldl
 *   abi_probe /path/libxycecinterface.so   ->  "xyce_cosim_abi=2"
 *
 * Exit status 0 when the symbol was called, 2 when the library does not load,
 * 3 when it does not export xyce_cosim_abi.  LD_LIBRARY_PATH decides which
 * libxyce.so the interface runs on.
 */

#include <dlfcn.h>
#include <stdio.h>

int main(int argc, char **argv)
{
   if (argc != 2) {
      fprintf(stderr, "usage: %s libxycecinterface.so\n", argv[0]);
      return 1;
   }
   void *lib = dlopen(argv[1], RTLD_LAZY | RTLD_GLOBAL);
   if (lib == NULL) {
      printf("dlopen failed: %s\n", dlerror());
      return 2;
   }
   int (*abi)(void) = (int (*)(void))dlsym(lib, "xyce_cosim_abi");
   if (abi == NULL) {
      printf("no xyce_cosim_abi in %s\n", argv[1]);
      return 3;
   }
   printf("xyce_cosim_abi=%d\n", (*abi)());
   return 0;
}
