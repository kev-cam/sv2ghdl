#!/bin/bash
# bfit --merge benchmark: node count + timesteps + wall time, transistor vs merged,
# for each --merge pattern (cascode / diff-pair / cross-coupled), against BOTH a
# native-device baseline and an OSDI/compiled-VA baseline. See merge.md for results.
#   N=400 bash merge_bench.sh        (needs ngspice>=45, openvaf, python3, bfit merge)
set -u
export PATH=/usr/bin:/bin:/usr/local/bin
BFIT=${BFIT:-$(cd "$(dirname "$0")/.." && pwd)/bfit.py}
OV=${OPENVAF:-/opt/openvaf/openvaf}
N=${N:-400}
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT; cd "$W" || exit 1

# --- device VAs used as the OSDI / compiled-VA baseline ("real" devices) ---
cat > pfet.va <<'EOF'
`include "disciplines.vams"
module pfet(d,g,s,b); inout d,g,s,b; electrical d,g,s,b;
 parameter real kp=200u from (0:inf); parameter real vtp=0.5; real vsg,vsd,vov,id;
 analog begin vsg=V(s,g); vsd=V(s,d); vov=vsg-vtp;
  id=(vov<=0.0)?0.0:((vsd>=vov)?0.5*kp*vov*vov:kp*(vov*vsd-0.5*vsd*vsd)); I(s,d)<+id; end
endmodule
EOF
"$OV" pfet.va >/dev/null 2>&1

# --- generate the merged cross-coupled component via `bfit merge` ---
printf '* x\n.model pm pmos level=1 kp=200u vto=-0.5\n.model nm nmos level=1 kp=200u vto=0.5\nVdd vdd 0 1.8\nMpa qa qb vdd vdd pm\nMna qa qb 0 0 nm\nMpb qb qa vdd vdd pm\nMnb qb qa 0 0 nm\n.end\n' > _xc.cir
python3 "$BFIT" merge _xc.cir -o _xcm.cir >/dev/null 2>&1; "$OV" xc_qa_qb_1.va >/dev/null 2>&1

rows(){ grep -aiE 'No. of Data Rows' "$1"|tail -1|grep -oE '[0-9]+'; }
runtime(){ local b=99 t0 t1 w; for r in 1 2; do t0=$(date +%s.%N); ngspice -b "$1">"$2" 2>&1; t1=$(date +%s.%N);
  w=$(echo "$t1-$t0"|bc); (($(echo "$w<$b"|bc)))&&b=$w; done; echo "$b"; }
emit(){ { printf '* bench\n'; cat "$1"; echo ".control"; [ -n "$2" ]&&echo "$2"; echo "$3"; echo "quit"; echo ".endc"; echo ".end"; } > "$1.r"; }
bench(){ emit "$2" "$3" "$4"; local tm; tm=$(runtime "$2.r" "$2.o");
  printf '%-30s %8s %8s %8.2f\n' "$1" "$5" "$(rows "$2.o")" "$tm"; }

# cascode arrays (Python heredoc writes the decks)
python3 - "$N" <<PY
import sys; N=int(sys.argv[1])
def w(fn,L): open(fn,"w").write("\n".join(L)+"\n.end\n")
# cascode, native level-1 baseline
L=["* casc",".model pm pmos level=1 kp=200u vto=-0.5","Vdd vdd 0 1.8"]
for i in range(N): L+=["Ii%d x%d 0 PWL(0 1u 40n 30u)"%(i,i),"Ci%d x%d 0 5f"%(i,i),
  "Mt%d v%d x%d vdd vdd pm w=6u l=1u"%(i,i,i),"Mb%d x%d x%d v%d vdd pm w=6u l=1u"%(i,i,i,i)]
w("casc_t.cir",L)
# cascode, OSDI baseline (pfet device instances)
L=["* casc osdi",".model pfetmod pfet()","Vdd vdd 0 1.8"]
for i in range(N): L+=["Ii%d x%d 0 PWL(0 1u 40n 30u)"%(i,i),"Ci%d x%d 0 5f"%(i,i),
  "Nt%d v%d x%d vdd vdd pfetmod"%(i,i,i),"Nb%d x%d x%d v%d vdd pfetmod"%(i,i,i,i)]
w("casc_o.cir",L)
# latch arrays (native vs merged OSDI)
for merged in (0,1):
  L=["* lat",".model pm pmos level=1 kp=200u vto=-0.5",".model nm nmos level=1 kp=200u vto=0.5",".model xcmod xc_qa_qb_1()","Vdd vdd 0 1.8"]; ic=[]
  for i in range(N):
    off=0.01*((i%9)-4); ic.append(".ic v(qa%d)=%.4f v(qb%d)=%.4f"%(i,0.9+off,i,0.9-off))
    L+=["Cqa%d qa%d 0 2f"%(i,i),"Cqb%d qb%d 0 2f"%(i,i)]
    L+= (["Nxc%d qa%d qb%d vdd 0 xcmod"%(i,i,i)] if merged else
         ["Mpa%d qa%d qb%d vdd vdd pm w=2u l=1u"%(i,i,i),"Mna%d qa%d qb%d 0 0 nm w=1u l=1u"%(i,i,i),
          "Mpb%d qb%d qa%d vdd vdd pm w=2u l=1u"%(i,i,i),"Mnb%d qb%d qa%d 0 0 nm w=1u l=1u"%(i,i,i)])
  w("lat_%s.cir"%("m" if merged else "t"),L+ic)
PY
python3 "$BFIT" merge casc_t.cir -o casc_m.cir 2>/dev/null   # square-law B-source (node eliminated)

echo "=== bfit --merge benchmark (N=$N) ==="
printf '%-30s %8s %8s %8s\n' "case" "nodes" "tsteps" "time(s)"
bench "cascode native (2 lvl1+V1)"  casc_t.cir "" "tran 0.1n 40n" $((2*N+1))
bench "cascode OSDI   (2 osdi+V1)"   casc_o.cir "pre_osdi pfet.osdi" "tran 0.1n 40n" $((2*N+1))
bench "cascode merged (1 elem, V1-)" casc_m.cir "" "tran 0.1n 40n" $((N+1))
bench "latch  native (4 lvl1)"       lat_t.cir  "" "tran 5p 8n uic" $((2*N))
bench "latch  merged (1 component)"  lat_m.cir  "pre_osdi xc_qa_qb_1.osdi" "tran 5p 8n uic" $((2*N))
echo "(see merge.md for interpretation)"
