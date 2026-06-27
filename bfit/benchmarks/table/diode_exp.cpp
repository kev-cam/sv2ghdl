// 2-node exp diode baseline (accurate mode). -DCLAMP ~ limexp limiting.
#include <cstring>
#include <cmath>
struct VaeState { double V[16]; double Vt; };
static const double ISAT=1e-14, VT=0.02585, CJO=0.0;
static inline double dio(double vd){
#ifdef CLAMP
    double a=vd/VT; if(a>40.0)a=40.0; return ISAT*(exp(a)-1.0);
#else
    return ISAT*(exp(vd/VT)-1.0);
#endif
}
extern "C" {
int vae_n_nodes(){return 2;}
int vae_n_branches(){return 2;}
void vae_eval(VaeState* s,double* F,double* Q){
    double vd=s->V[0]-s->V[1], id=dio(vd);
    F[0]=id; F[1]=-id; Q[0]=CJO*vd; Q[1]=-CJO*vd;
}
void vae_jacobian(VaeState* s,double* dF,double* dQ){
    const double dv=1e-6; VaeState sp; double F0[2],Q0[2],Fp[2],Qp[2];
    memset(dF,0,4*sizeof(double)); memset(dQ,0,4*sizeof(double));
    vae_eval(s,F0,Q0);
    for(int j=0;j<2;j++){ sp=*s; sp.V[j]+=dv; vae_eval(&sp,Fp,Qp);
        for(int i=0;i<2;i++){ dF[i*2+j]=(Fp[i]-F0[i])/dv; dQ[i*2+j]=(Qp[i]-Q0[i])/dv; } }
}
}
