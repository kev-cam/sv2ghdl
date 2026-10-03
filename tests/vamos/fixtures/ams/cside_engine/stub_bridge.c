/*
 * TEST ONLY.  A libcosim_bridge.so that predates the finish protocol, for the
 * ABI-handshake cases of run_cside.py: it has the four entry points an old
 * bridge had, and cosim_bridge_abi() only when built with -DSTUB_ABI=<n>.
 * nvc must refuse it with "co-simulation ABI mismatch" before it starts.
 *
 *   cc -O2 -shared -fPIC -o <dir>/libcosim_bridge.so stub_bridge.c [-DSTUB_ABI=1]
 */

typedef void (*bridge_deposit_fn)(void *ctx, double voltage);
typedef int (*bridge_apply_fn)(void *ctx, double frac);
typedef int (*bridge_stepper_fn)(void *ctx, double t_prev_s, double t_s,
                                 int nsamples, bridge_apply_fn apply,
                                 void *apply_ctx, double *t_evt_s);

static int nsignals;

int cosim_bridge_register(const char *name, int dir, double initial_voltage,
                          bridge_deposit_fn deposit_fn, void *deposit_ctx)
{
   (void)name; (void)dir; (void)initial_voltage; (void)deposit_fn;
   (void)deposit_ctx;
   return nsignals < 256 ? nsignals++ : -1;
}

int cosim_bridge_update_d2a(int idx, double voltage, double next_time_s,
                            double now_s)
{
   (void)idx; (void)voltage; (void)next_time_s; (void)now_s;
   return 0;
}

void cosim_bridge_reset(void)
{
   nsignals = 0;
}

void cosim_bridge_set_stepper(bridge_stepper_fn fn, void *ctx)
{
   (void)fn; (void)ctx;
}

#ifdef STUB_ABI
int cosim_bridge_abi(void)
{
   return STUB_ABI;
}
#endif
