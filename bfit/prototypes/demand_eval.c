// Demand-driven (pull/backward) vs eager (push/forward) evaluation — WALLCLOCK.
//
// Verilator is push: compiled straight-line code re-evaluates the whole design
// every cycle, forward in time. Its per-node eval is nearly free (no dispatch,
// no memo). A pull evaluator computes a signal only when observed, recursing
// backward through its cone, memoised per (node,time). It does far FEWER evals
// (dead logic and unobserved time cost nothing) but each eval is dearer (a memo
// probe + a call). So the honest question is not eval count, it is wallclock:
// pull wins only where its eval-count reduction beats its per-eval overhead.
//
// This times both, on the same netlist, across observation densities, and
// verifies pull == push at every observed point.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

enum { IN, REG, GATE };
enum { OP_XOR2, OP_SUMN, OP_INC };

typedef struct { uint8_t kind, op; int a0, a1, npred, poff; } Node;

static Node  *node; static int *preds; static int N, NP;
static int    C;                 // cycles
static uint8_t *stimtab;         // C x live_width stimulus

static inline uint8_t stim(int t, int i){ return (uint8_t)(((t*2654435761u)+(i*40503u))>>3); }

// ---- build: live pipeline (feeds output) + dead free-running logic ----
static int build(int depth, int dead, int width){
   int cap = (width*(2*depth+1)) + 1 + dead*2 + 16;
   node  = calloc(cap, sizeof(Node));
   preds = calloc(cap*(width>4?width:4)+16, sizeof(int));
   N = 0; NP = 0;
   int *ins = malloc(width*sizeof(int));
   for(int i=0;i<width;i++){ node[N]=(Node){IN,0,i,0,0,0}; ins[i]=N++; }
   int *stage = malloc(width*sizeof(int)); memcpy(stage, ins, width*sizeof(int));
   int *mixed = malloc(width*sizeof(int));
   for(int d=0; d<depth; d++){
      for(int i=0;i<width;i++){
         node[N]=(Node){GATE,OP_XOR2, stage[i], stage[(i+1)%width], 2, 0}; mixed[i]=N++;
      }
      for(int i=0;i<width;i++){ node[N]=(Node){REG,0, mixed[i],0,0,0}; stage[i]=N++; }
   }
   int out = N; node[N]=(Node){GATE,OP_SUMN,0,0,width,NP}; N++;
   for(int i=0;i<width;i++) preds[NP++]=stage[i];
   for(int d=0; d<dead; d++){                      // dead: counter regs feeding nothing observed
      node[N]=(Node){GATE,OP_INC, ins[d%width],0,1,0}; int g=N++;
      node[N]=(Node){REG,0, g,0,0,0}; N++;
   }
   free(ins); free(stage); free(mixed);
   return out;
}

static inline uint8_t gate_eval(int n, uint8_t p0, uint8_t p1, const uint8_t *pv){
   switch(node[n].op){
      case OP_XOR2: return p0 ^ p1;
      case OP_INC:  return (uint8_t)(p0 + 1);
      case OP_SUMN: { unsigned s=0; for(int k=0;k<node[n].npred;k++) s+=pv[k]; return (uint8_t)s; }
   }
   return 0;
}

// ---- PUSH: forward, every node every cycle. O(nodes) memory (Verilator-like) ----
static uint64_t run_push(int out, uint8_t *outbuf){
   uint8_t *cur=calloc(N,1), *nxt=calloc(N,1); uint64_t evals=0;
   uint8_t tmp[64];
   for(int t=0;t<C;t++){
      for(int n=0;n<N;n++){
         if(node[n].kind==IN) cur[n]=stim(t,node[n].a0);
         else if(node[n].kind==GATE){
            if(node[n].op==OP_SUMN){ for(int k=0;k<node[n].npred;k++) tmp[k]=cur[preds[node[n].poff+k]];
                                     cur[n]=gate_eval(n,0,0,tmp); }
            else cur[n]=gate_eval(n,cur[node[n].a0],cur[node[n].a1],0);
            evals++;
         }
      }
      for(int n=0;n<N;n++) if(node[n].kind==REG) nxt[n]=cur[node[n].a0];
      outbuf[t]=cur[out];
      for(int n=0;n<N;n++) if(node[n].kind==REG) cur[n]=nxt[n];
   }
   free(cur); free(nxt); return evals;
}

// ---- PULL: backward, memoised per (node,time). O(nodes*cycles) memo ----
static uint8_t *M; static uint8_t *done; static uint64_t pull_evals;
static uint8_t value(int n, int t){
   if(t<0) return 0;
   size_t k=(size_t)n*C+t;
   if(done[k]) return M[k];
   uint8_t v;
   if(node[n].kind==IN) v=stim(t,node[n].a0);
   else if(node[n].kind==REG) v=value(node[n].a0, t-1);         // look BACKWARD
   else {
      if(node[n].op==OP_SUMN){ uint8_t tmp[64]; for(int j=0;j<node[n].npred;j++) tmp[j]=value(preds[node[n].poff+j],t);
                               v=gate_eval(n,0,0,tmp); }
      else v=gate_eval(n,value(node[n].a0,t),value(node[n].a1,t),0);
   }
   pull_evals++; M[k]=v; done[k]=1; return v;
}
static uint64_t run_pull(int out, int stride, uint8_t *outbuf){
   M=calloc((size_t)N*C,1); done=calloc((size_t)N*C,1); pull_evals=0;
   for(int t=(stride<0? C-1:0); t<C; t+= (stride<0?C:stride)) outbuf[t]=value(out,t);
   free(M); free(done); return pull_evals;
}

static double now(){ struct timespec s; clock_gettime(CLOCK_MONOTONIC,&s); return s.tv_sec+s.tv_nsec/1e9; }

static void reset_net(){ free(node); free(preds); node=NULL; preds=NULL; N=0; NP=0; }

// crossover: at every-cycle observation (pull's HARDEST case), how much of the
// design must be dead/unobserved for pull to beat push in wallclock?
static void crossover(int depth,int width){
   C=5000;
   printf("CROSSOVER (observe EVERY cycle — pull's worst case):\n");
   printf("  %-10s %-8s %10s %10s  %s\n","dead regs","%dead","push(s)","pull(s)","pull vs push");
   int deads[]={0,200,500,1000,2000,4000,8000};
   for(int i=0;i<7;i++){
      int out=build(depth,deads[i],width);
      uint8_t *pb=calloc(C,1), *lb=calloc(C,1);
      double s=now(); run_push(out,pb); double pt=now()-s;
      s=now(); run_pull(out,1,lb); double lt=now()-s;
      double pctdead = 100.0*(2.0*deads[i])/N;
      printf("  %-10d %6.1f%% %9.4f %9.4f  %.2fx %s\n", deads[i], pctdead, pt, lt,
             pt/lt, pt>lt?"FASTER":"slower");
      free(pb); free(lb); reset_net();
   }
   printf("\n");
}

int main(){
   crossover(20,8);
   int depth=20, dead=4000, width=8; C=5000;
   int out=build(depth,dead,width);
   uint8_t *pb=calloc(C,1), *lb=calloc(C,1);

   double t0=now(); uint64_t pe=run_push(out,pb); double push_t=now()-t0;

   printf("design: %d nodes (%d dead regs), %d cycles, pipeline depth %d\n\n", N, dead, C, depth);
   printf("PUSH (forward, all nodes every cycle): %.4fs, %llu node-evals\n\n",
          push_t, (unsigned long long)pe);
   printf("%-20s %12s %10s  %-9s %s\n","PULL observe","evals","wallclock","correct","vs push");
   struct { const char*name; int stride; } obs[] = {
      {"every cycle",1},{"every 10th",10},{"every 100th",100},{"final only",-1} };
   for(int i=0;i<4;i++){
      double s=now(); uint64_t ev=run_pull(out,obs[i].stride,lb); double pt=now()-s;
      int ok=1;
      for(int t=(obs[i].stride<0?C-1:0); t<C; t+=(obs[i].stride<0?C:obs[i].stride)) if(lb[t]!=pb[t]) ok=0;
      printf("%-20s %12llu %9.4fs  %-9s %.2fx %s\n", obs[i].name,
             (unsigned long long)ev, pt, ok?"YES":"NO!!",
             push_t/pt, push_t>pt?"FASTER":"slower");
   }
   printf("\nHONEST NOTE: pull's per-eval (memo probe + recursion) is dearer than a\n"
          "compiled push node-eval, so fewer evals != proportional speedup. Pull wins\n"
          "wallclock only where observation/time is sparse enough that the eval-count\n"
          "reduction beats the per-eval overhead. Compiling the pull cones (removing the\n"
          "memo/recursion overhead) is what would extend the win to dense observation.\n");
   return 0;
}
