// Proof of concept: cached-state gate evaluation vs traditional
// Shows flat execution time regardless of input count

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

// --- Cached-state gate evaluator ---

typedef struct gate gate_t;

// Per-input update function: knows its slot, updates cached state
typedef void (*input_update_fn)(gate_t *g, int new_val);

typedef enum { GATE_AND, GATE_OR, GATE_XOR, GATE_NAND, GATE_NOR } gate_type_t;

typedef struct gate {
   gate_type_t type;
   int         n_inputs;
   int         count_ones;    // AND/OR: count of high inputs
   int         parity;        // XOR: running parity
   int         output;        // current output value
   int        *input_vals;    // cached input values
   // Downstream: who to notify when output changes
   gate_t     *downstream_gate;
   int         downstream_slot;
} gate_t;

static inline int gate_compute_output(gate_t *g)
{
   switch (g->type) {
   case GATE_AND:  return g->count_ones == g->n_inputs;
   case GATE_NAND: return g->count_ones != g->n_inputs;
   case GATE_OR:   return g->count_ones > 0;
   case GATE_NOR:  return g->count_ones == 0;
   case GATE_XOR:  return g->parity;
   }
   return 0;
}

// The per-input update: O(1) regardless of fan-in
static void gate_input_updated(gate_t *g, int slot, int new_val)
{
   int old_val = g->input_vals[slot];
   if (old_val == new_val) return;
   g->input_vals[slot] = new_val;

   // Update cached aggregate
   int delta = new_val - old_val;
   g->count_ones += delta;
   if (delta & 1) g->parity ^= 1;

   int new_output = gate_compute_output(g);
   if (new_output != g->output) {
      g->output = new_output;
      // Propagate to downstream
      if (g->downstream_gate)
         gate_input_updated(g->downstream_gate, g->downstream_slot, new_output);
   }
}

// --- Traditional gate evaluator (re-evaluate all inputs) ---

typedef struct trad_gate {
   gate_type_t type;
   int         n_inputs;
   int        *input_vals;
   int         output;
   struct trad_gate *downstream_gate;
   int         downstream_slot;
} trad_gate_t;

static int trad_evaluate(trad_gate_t *g)
{
   int result;
   switch (g->type) {
   case GATE_AND:
   case GATE_NAND:
      result = 1;
      for (int i = 0; i < g->n_inputs; i++)
         result &= g->input_vals[i];
      if (g->type == GATE_NAND) result = !result;
      return result;
   case GATE_OR:
   case GATE_NOR:
      result = 0;
      for (int i = 0; i < g->n_inputs; i++)
         result |= g->input_vals[i];
      if (g->type == GATE_NOR) result = !result;
      return result;
   case GATE_XOR:
      result = 0;
      for (int i = 0; i < g->n_inputs; i++)
         result ^= g->input_vals[i];
      return result;
   }
   return 0;
}

static void trad_input_updated(trad_gate_t *g, int slot, int new_val)
{
   g->input_vals[slot] = new_val;
   int new_output = trad_evaluate(g);
   if (new_output != g->output) {
      g->output = new_output;
      if (g->downstream_gate)
         trad_input_updated(g->downstream_gate, g->downstream_slot, new_output);
   }
}

// --- Benchmark ---

static double time_diff(struct timespec *start, struct timespec *end)
{
   return (end->tv_sec - start->tv_sec) + (end->tv_nsec - start->tv_nsec) / 1e9;
}

static void bench_cached(int n_inputs, int n_iterations)
{
   // Chain of 4 gates: input_gate -> gate2 -> gate3 -> gate4
   gate_t gates[4];
   for (int i = 0; i < 4; i++) {
      gates[i].type = (i % 2 == 0) ? GATE_AND : GATE_XOR;
      gates[i].n_inputs = (i == 0) ? n_inputs : 2;
      gates[i].count_ones = 0;
      gates[i].parity = 0;
      gates[i].output = 0;
      gates[i].input_vals = calloc(gates[i].n_inputs, sizeof(int));
      gates[i].downstream_gate = (i < 3) ? &gates[i+1] : NULL;
      gates[i].downstream_slot = 0;
   }

   struct timespec t0, t1;
   clock_gettime(CLOCK_MONOTONIC, &t0);

   // Toggle random inputs on the first gate
   for (int i = 0; i < n_iterations; i++) {
      int slot = i % n_inputs;
      int new_val = (gates[0].input_vals[slot] == 0) ? 1 : 0;
      gate_input_updated(&gates[0], slot, new_val);
   }

   clock_gettime(CLOCK_MONOTONIC, &t1);
   double elapsed = time_diff(&t0, &t1);

   printf("  cached %3d-input: %.3fs  (%d Meval/s)  out=%d\n",
          n_inputs, elapsed,
          (int)(n_iterations / elapsed / 1e6),
          gates[3].output);

   for (int i = 0; i < 4; i++) free(gates[i].input_vals);
}

static void bench_traditional(int n_inputs, int n_iterations)
{
   trad_gate_t gates[4];
   for (int i = 0; i < 4; i++) {
      gates[i].type = (i % 2 == 0) ? GATE_AND : GATE_XOR;
      gates[i].n_inputs = (i == 0) ? n_inputs : 2;
      gates[i].output = 0;
      gates[i].input_vals = calloc(gates[i].n_inputs, sizeof(int));
      gates[i].downstream_gate = (i < 3) ? &gates[i+1] : NULL;
      gates[i].downstream_slot = 0;
   }

   struct timespec t0, t1;
   clock_gettime(CLOCK_MONOTONIC, &t0);

   for (int i = 0; i < n_iterations; i++) {
      int slot = i % n_inputs;
      int new_val = (gates[0].input_vals[slot] == 0) ? 1 : 0;
      trad_input_updated(&gates[0], slot, new_val);
   }

   clock_gettime(CLOCK_MONOTONIC, &t1);
   double elapsed = time_diff(&t0, &t1);

   printf("  trad   %3d-input: %.3fs  (%d Meval/s)  out=%d\n",
          n_inputs, elapsed,
          (int)(n_iterations / elapsed / 1e6),
          gates[3].output);

   for (int i = 0; i < 4; i++) free(gates[i].input_vals);
}

int main(void)
{
   const int N = 50000000;
   int fan_ins[] = { 2, 4, 8, 16, 32, 64, 128, 256 };

   printf("=== Gate evaluation benchmark: %dM iterations ===\n\n", N/1000000);

   for (int i = 0; i < (int)(sizeof(fan_ins)/sizeof(fan_ins[0])); i++) {
      int fi = fan_ins[i];
      bench_traditional(fi, N);
      bench_cached(fi, N);
      printf("\n");
   }

   return 0;
}
