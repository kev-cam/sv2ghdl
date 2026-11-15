#!/bin/bash
# attempt 4 remote run (manual restart with the runtime library on the loader path)
source /opt/aws_neuronx_venv_jax_0_10/bin/activate
export LD_LIBRARY_PATH=/opt/aws/neuron/lib:${LD_LIBRARY_PATH:-} NEURON_LOGICAL_NC_CONFIG=1 NEURON_PLATFORM_TARGET_OVERRIDE=trn1 HW2_PROFILE=0
export PATH=/opt/aws/neuron/bin:$PATH
cd ~/sa; rm -rf ~/sa/out
echo "=== start $(date -Is)"; python -c "import nki.runtime; print('nki.runtime import ok')"
python nki_sa_hw2.py 128 256 5 --kernels sa,sa2,sa3 --out ~/sa/out
echo "=== small done $(date -Is)"
python nki_sa_hw2.py 128 8192 5 --kernels sa3 --out ~/sa/out
echo "=== big done $(date -Is)"
echo "=== profile pass"
for n in ~/sa/out/*.neff; do
  [ -f "$n" ] || continue
  b=${n%.neff}; echo "--- $n $(date -Is)"
  timeout 300 neuron-profile capture -n "$n" -s "$b.ntff" --ignore-exec-errors 2>&1 | tail -3
  timeout 300 neuron-profile view -n "$n" -s "$b.ntff" --output-format summary-text 2>&1 | head -60
  timeout 300 neuron-profile view -n "$n" -s "$b.ntff" --output-format summary-json > "$b.summary.json" 2>/dev/null
done
echo "=== profile done $(date -Is)"
cd ~/sa/out && tar czf ~/sa/hw2_artifacts.tgz --exclude='*.mlir' --exclude=ir_dumps --exclude='*.colz' . && ls -la ~/sa/hw2_artifacts.tgz
echo "=== remote done $(date -Is)"
