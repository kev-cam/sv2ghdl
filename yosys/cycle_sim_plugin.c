// cycle_sim_plugin.c — VHPI plugin that loads a compiled state machine .so
// and swaps it into NVC's process vtable for cycle-based execution.
//
// Usage: nvc -r --load=./libcycle_sim.so <top_entity>
//
// The plugin:
//   1. At start of simulation, finds all processes and signals
//   2. Looks for a compiled state machine .so (sm_<module>.so)
//   3. If found, maps signal pointers and swaps the process vtable
//   4. Falls back to JIT if no .so exists or if X/Z is detected

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include <stdint.h>

// NVC headers
#include "rt/model.h"
#include "rt/structs.h"

// State machine interface (exported by generated .so)
typedef struct {
    int n_regs;
    int n_inputs;
    int n_outputs;
    const char **reg_names;
    const char **input_names;
    const char **output_names;
    int *reg_widths;
    int *input_widths;
    int *output_widths;
} sm_info_t;

typedef void (*sm_init_fn)(void **signal_ptrs, int n_signals);
typedef void (*sm_eval_fn)(void);
typedef void (*sm_reset_fn)(void);
typedef const sm_info_t *(*sm_get_info_fn)(void);

// Per-module state machine binding
typedef struct {
    void *dl_handle;
    sm_eval_fn eval;
    sm_reset_fn reset;
    sm_get_info_fn get_info;
    rt_proc_t *proc;

    // Signal mappings: pointers into NVC signal storage
    rt_signal_t **signals;
    int n_signals;
} cycle_sim_binding_t;

// Process eval via compiled state machine
static void proc_eval_cycle_sim(rt_model_t *m, rt_proc_t *proc)
{
    // The binding is stashed after the vtable
    // For now, use a global (single module support)
    extern cycle_sim_binding_t *g_binding;

    if (g_binding && g_binding->eval) {
        g_binding->eval();

        // TODO: deposit changed outputs back to NVC signals
        // For now the state machine writes directly to mapped pointers
    }
}

static const rt_proc_vtable_t cycle_sim_vtable = {
    .eval  = proc_eval_cycle_sim,
    .reset = NULL,  // filled in at bind time
};

cycle_sim_binding_t *g_binding = NULL;

// Try to load and bind a state machine .so for a process
static cycle_sim_binding_t *try_bind_sm(const char *so_path, rt_proc_t *proc)
{
    void *dl = dlopen(so_path, RTLD_NOW);
    if (!dl) {
        fprintf(stderr, "cycle_sim: cannot load %s: %s\n", so_path, dlerror());
        return NULL;
    }

    sm_eval_fn eval = dlsym(dl, "sm_eval_mapped");
    sm_reset_fn reset = dlsym(dl, "sm_reset");

    if (!eval) {
        fprintf(stderr, "cycle_sim: %s has no sm_eval_mapped\n", so_path);
        dlclose(dl);
        return NULL;
    }

    cycle_sim_binding_t *b = calloc(1, sizeof(*b));
    b->dl_handle = dl;
    b->eval = eval;
    b->reset = reset;
    b->proc = proc;

    fprintf(stderr, "cycle_sim: loaded %s for process %s\n",
            so_path, istr(proc->name));

    return b;
}

// Install the binding: swap the process vtable
static void install_binding(cycle_sim_binding_t *b)
{
    g_binding = b;
    proc_set_vtable(b->proc, &cycle_sim_vtable);
    fprintf(stderr, "cycle_sim: installed fast path for %s\n",
            istr(b->proc->name));
}

// Revert to JIT
static void uninstall_binding(cycle_sim_binding_t *b)
{
    proc_reset_vtable(b->proc);
    g_binding = NULL;
    fprintf(stderr, "cycle_sim: reverted %s to JIT\n",
            istr(b->proc->name));
}
