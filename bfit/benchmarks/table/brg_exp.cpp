// exp-diode full-bridge baseline (same topology as emit_bridge_table_so).
// -DCLAMP limits the exp argument (~ what limexp does); without it, raw exp.
#include <cstring>
#include <cmath>
struct VaeState { double V[16]; double Vt; };
static const double ISAT=1e-14, VT=0.02585, CJO=2e-11, GBLEED=0.0;
static inline double dio(double vd){
#ifdef CLAMP
    double a = vd/VT; if (a > 40.0) a = 40.0; return ISAT*(exp(a)-1.0);
#else
    return ISAT*(exp(vd/VT)-1.0);
#endif
}
extern "C" {
int vae_n_nodes(){return 4;}
int vae_n_branches(){return 4;}
void vae_eval(VaeState* s, double* F, double* Q){
    double Va=s->V[0],Vb=s->V[1],Vp=s->V[2],Vn=s->V[3];
    double i1=dio(Va-Vp), i2=dio(Vb-Vp), i3=dio(Vn-Va), i4=dio(Vn-Vb);
    double ir=GBLEED*(Va-Vb);
    F[0]=i1-i3+ir; F[1]=i2-i4-ir; F[2]=-i1-i2; F[3]=i3+i4;
    double q1=CJO*(Va-Vp),q2=CJO*(Vb-Vp),q3=CJO*(Vn-Va),q4=CJO*(Vn-Vb);
    Q[0]=q1-q3; Q[1]=q2-q4; Q[2]=-q1-q2; Q[3]=q3+q4;
}
void vae_jacobian(VaeState* s, double* dFdV, double* dQdV){
    const double dv=1e-6; VaeState sp; double F0[4],Q0[4],Fp[4],Qp[4];
    memset(dFdV,0,16*sizeof(double)); memset(dQdV,0,16*sizeof(double));
    vae_eval(s,F0,Q0);
    for(int j=0;j<4;j++){ sp=*s; sp.V[j]+=dv; vae_eval(&sp,Fp,Qp);
        for(int i=0;i<4;i++){ dFdV[i*4+j]=(Fp[i]-F0[i])/dv; dQdV[i*4+j]=(Qp[i]-Q0[i])/dv; } }
}
}
