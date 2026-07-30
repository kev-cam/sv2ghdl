// COMPLETE verification of gen_statemachine's worbits single-word peephole.
//
// The emitter replaces a call
//     worbits(d, doff, s, soff, w)
// with the folded statement
//     d[doff>>5] |= ((s[soff>>5] >> (soff&31)) & MASK) << (doff&31);
// whenever (doff&31)+w <= 32 AND (soff&31)+w <= 32.
//
// This is not a spot check.  It is EXHAUSTIVE over the whole parameter space,
// and complete over the data:
//
//  * SHAPE: every (doff&31, soff&31, w) triple in 0..31 x 0..31 x 1..32.
//  * DATA: both forms are bitwise-linear in the source -- each result bit is a
//    copy of one source bit OR'd into place, with no carry or interaction
//    between bits.  Two bitwise-linear functions of a 32-bit word are equal for
//    all 2^32 inputs iff they agree on the 32 single-bit basis vectors and on
//    zero, so testing those 33 patterns is a proof, not a sample.  Both the
//    all-ones and a mixed pattern are included as redundant belt-and-braces.
//  * DESTINATION: worbits ORs into d, so the pre-existing content must be
//    preserved.  Each case is run against several distinct d values.
//
// It ALSO checks the guard is NECESSARY: outside the condition the folded form
// must differ from worbits somewhere, which is what makes the fallback to the
// real call load-bearing rather than dead code.
//
// Build:  cc -O2 -o /tmp/prove_worbits prove_worbits_peephole.c && /tmp/prove_worbits
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdbool.h>

// ---- verbatim from gen_statemachine.cpp's emitted prelude -------------------
static void worbits(uint32_t *d, int doff, const uint32_t *s, int soff, int w)
{
   while (w > 0) {
      int db = doff & 31, sb = soff & 31;
      int n = 32 - (db > sb ? db : sb);
      if (n > w) n = w;
      uint32_t m = (n >= 32) ? 0xffffffffu : ((1u << n) - 1u);
      d[doff >> 5] |= ((s[soff >> 5] >> sb) & m) << db;
      doff += n; soff += n; w -= n;
   }
}

// ---- what the peephole emits ------------------------------------------------
static void folded(uint32_t *d, int doff, const uint32_t *s, int soff, int w)
{
   const unsigned mask = (w >= 32) ? 0xffffffffu : ((1u << w) - 1u);
   d[doff >> 5] |= ((s[soff >> 5] >> (soff & 31)) & mask) << (doff & 31);
}

#define NW 8   // words in each buffer; offsets stay well inside

int main(void)
{
   // 33-pattern complete basis for a bitwise-linear function, plus two extras.
   uint32_t pats[35];
   int npat = 0;
   pats[npat++] = 0u;
   for (int b = 0; b < 32; b++) pats[npat++] = 1u << b;
   pats[npat++] = 0xFFFFFFFFu;
   pats[npat++] = 0xA5C33C5Au;

   const uint32_t dseed[] = { 0x00000000u, 0xFFFFFFFFu, 0xDEADBEEFu };

   long checked = 0, mism_in = 0, mism_out = 0, cases_out = 0;

   for (int db = 0; db < 32; db++) {
      for (int sb = 0; sb < 32; sb++) {
         for (int w = 1; w <= 32; w++) {
            const bool guarded = (db + w <= 32) && (sb + w <= 32);
            bool differs_here = false;

            for (int p = 0; p < npat; p++) {
               for (unsigned di = 0; di < sizeof dseed / sizeof dseed[0]; di++) {
                  uint32_t a[NW], b[NW], src[NW];
                  for (int i = 0; i < NW; i++) {
                     a[i] = b[i] = dseed[di];
                     src[i] = pats[p];
                  }
                  // place the ranges inside word 2 (dest) / word 3 (src) so the
                  // >>5 indices are non-zero and a stray carry into a
                  // neighbouring word would be visible
                  const int doff = 2 * 32 + db;
                  const int soff = 3 * 32 + sb;
                  worbits(a, doff, src, soff, w);
                  folded (b, doff, src, soff, w);
                  checked++;
                  if (memcmp(a, b, sizeof a) != 0) {
                     differs_here = true;
                     if (guarded && mism_in++ < 5)
                        printf("  MISMATCH INSIDE GUARD db=%d sb=%d w=%d "
                               "pat=0x%08x d=0x%08x\n", db, sb, w, pats[p],
                               dseed[di]);
                  }
               }
            }
            if (!guarded) { cases_out++; if (differs_here) mism_out++; }
         }
      }
   }

   printf("exhaustive over (db,sb,w) = 32x32x32, %d data patterns x %zu dest "
          "seeds\n", npat, sizeof dseed / sizeof dseed[0]);
   printf("  comparisons run                     : %ld\n", checked);
   printf("  mismatches INSIDE the guard         : %ld   (must be 0)\n", mism_in);
   printf("  shapes outside the guard            : %ld\n", cases_out);
   printf("  of those, shapes where folded DIFFERS: %ld   (guard is load-bearing)\n",
          mism_out);

   if (mism_in != 0) {
      printf("RESULT: PEEPHOLE IS WRONG\n");
      return 1;
   }
   if (mism_out == 0) {
      printf("RESULT: guard never matters — the fallback is dead code, "
             "which contradicts the emitter's assumption\n");
      return 1;
   }
   printf("RESULT: PROVED — folded form is equivalent to worbits for EVERY "
          "shape satisfying the guard, and the guard is necessary\n");
   return 0;
}
