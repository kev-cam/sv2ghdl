#!/bin/bash
# Follow-up run of 2026-11-18 on the Neuron DLAMI (trn1.2xlarge): sa3 reference, uncertainty plane, deep chains.
VENV=$(ls -d /opt/aws_neuronx_venv_* 2>/dev/null | head -1); echo "venv $VENV"; source $VENV/bin/activate
export NEURON_LOGICAL_NC_CONFIG=1 NEURON_PLATFORM_TARGET_OVERRIDE=trn1 LD_LIBRARY_PATH=/opt/aws/neuron/lib:${LD_LIBRARY_PATH:-}
export PATH=/opt/aws/neuron/bin:$PATH
cd ~/sa; rm -rf ~/sa/out3
echo "=== start $(date -Is)"
pip install -q --extra-index-url https://pip.repos.neuron.amazonaws.com "nki==0.7.0+37713544100.gfede04bb" "neuronx-cc==2.28.7631.0+8f7b0cf5"
pip install -q --force-reinstall --no-deps --extra-index-url https://pip.repos.neuron.amazonaws.com "nrtpy==2.35.33+d2725ccb"
pip list 2>/dev/null | grep -i -E "^(nki|neuronx-cc|nrtpy) "
python -c "import nki.runtime; print('nki.runtime import ok')"
neuron-ls 2>/dev/null | head -8
echo "=== run $(date -Is)"
python nki_hw3.py 5 --out ~/sa/out3 --cases sa3,sa3u,deep4,deep19,deep137,sa3u8k
echo "=== run done $(date -Is)"
cd ~/sa/out3 && tar czf ~/sa/hw3_artifacts.tgz --exclude='*.mlir' --exclude=ir_dumps --exclude='*.colz' . && ls -la ~/sa/hw3_artifacts.tgz
echo "=== remote done $(date -Is)"
