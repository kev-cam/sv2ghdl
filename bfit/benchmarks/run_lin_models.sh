#!/bin/bash
export PATH=/usr/bin:/bin:/usr/local/bin
X=/tmp/xrun_model.sh
for m in rlc_filter rectifier inv_chain ring_osc ota_5t bjt_amp; do
  ng=$(bash $X ngspice "$m.cir" 180)
  xy=$(bash $X xyce "$m.cir" 180)
  ngb=$(bash $X ngspice_bfit "$m.cir" 180)
  xyb=$(bash $X xyce_bfit "$m.cir" 180)
  echo "$m  ngspice=$ng  xyce=$xy  ngspice_bfit=$ngb  xyce_bfit=$xyb"
done
echo "=== MPI sanity on the largest model (inv_chain, 200 Tx) np=2,4,8 ==="
for np in 2 4 8; do
  t=$(bash $X xyce_mpi$np inv_chain.cir 120)
  echo "  inv_chain np=$np: $t"
done
