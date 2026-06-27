// per-eval throughput driver: dlopen a VAE-ABI .so, time N vae_eval calls.
// Operating point kept in-range so the exp baseline does not overflow (fair).
#include <dlfcn.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <chrono>
struct VaeState { double V[16]; double Vt; };
typedef void (*evalfn)(VaeState*, double*, double*);
int main(int argc, char** argv){
    if(argc<2){ printf("usage: %s <so> [N]\n", argv[0]); return 2; }
    void* h = dlopen(argv[1], RTLD_NOW);
    if(!h){ printf("dlopen fail: %s\n", dlerror()); return 1; }
    evalfn ev = (evalfn)dlsym(h, "vae_eval");
    if(!ev){ printf("no vae_eval\n"); return 1; }
    long N = argc>2 ? atol(argv[2]) : 20000000L;
    VaeState s; memset(&s,0,sizeof(s)); double F[4],Q[4], acc=0.0;
    auto t0=std::chrono::high_resolution_clock::now();
    for(long i=0;i<N;i++){
        double v = 0.0006*(i%1000) + 0.2;   // Vd in [0.2,0.8) -> exp safe
        s.V[0]=v; s.V[1]=0; s.V[2]=0.3; s.V[3]=0;
        ev(&s,F,Q); acc+=F[2];
    }
    auto t1=std::chrono::high_resolution_clock::now();
    double ns=std::chrono::duration<double,std::nano>(t1-t0).count()/N;
    printf("  %-28s %7.3f ns/eval   (acc=%.4g)\n", argv[1], ns, acc);
    dlclose(h); return 0;
}
