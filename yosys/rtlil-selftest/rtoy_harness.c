// Driving harness: reset, then 64 cycles of varying d; print outputs per
// cycle.  Compiled twice: -DMODEL_C for the text-path C and builder C.
#include <stdio.h>
#include <stdint.h>
#define SM_NO_MAIN
#include MODEL_C
int main(void)
{
   state_t s; inputs_t in; outputs_t o;
   sm_reset(&s);
   for (int cyc = 0; cyc < 64; cyc++) {
      in._d = (uint64_t)((cyc * 37 + 11) & 0xff);
      sm_comb(&s, &in, &o);
      printf("%02d d=%02x q=%02llx y=%02llx\n", cyc, (unsigned)in._d,
             (unsigned long long)o._q, (unsigned long long)o._y);
      sm_clock(&s, &in);
   }
   return 0;
}
