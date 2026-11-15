#!/bin/bash
# Launch a trn1.2xlarge (us-east-1) with the Neuron DLAMI, run the device-clock harness nki_sa_hw2.py on it, fetch results, terminate.
# Usage: hw_launch.sh [ondemand|spot] [max_minutes]      (needs the Trn quota > 0; dry-run with HW_DRY=1)
# HW_KEEP=1: if the run produced no device timing, leave the instance up for debugging (user-data still halts it at max_minutes).
set -u
MARKET=${1:-ondemand}; MAXMIN=${2:-60}
export AWS_PROFILE=fpga AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=
KEY=ldx-fpga; PEM=$HOME/.ssh/ldx-fpga; TYPE=trn1.2xlarge; SG=ldx-trn-ssh; OUT=/opt/trn/hw_results; mkdir -p "$OUT"
TS=$(date +%Y%m%d_%H%M%S)
AMI=$(aws ec2 describe-images --owners amazon --filters 'Name=name,Values=Deep Learning AMI Neuron (Ubuntu 24.04)*' Name=state,Values=available \
      --query 'reverse(sort_by(Images,&CreationDate))[0].[ImageId,Name]' --output text)
AMI_ID=${AMI%%	*}; echo "AMI: $AMI"
MYIP=$(curl -s --max-time 5 https://checkip.amazonaws.com)/32
SGID=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$SG --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null)
if [ -z "$SGID" ] || [ "$SGID" = None ]; then
    SGID=$(aws ec2 create-security-group --group-name $SG --description "ssh for Trainium test box" --query GroupId --output text)
fi
aws ec2 authorize-security-group-ingress --group-id "$SGID" --protocol tcp --port 22 --cidr "$MYIP" >/dev/null 2>&1 || true
echo "SG: $SGID (ssh from $MYIP)"
UD=$(printf '#!/bin/bash\nshutdown -h +%d\n' "$MAXMIN" | base64 -w0)      # hard cap on cost: the box halts (=terminates) after MAXMIN
MKT=""; [ "$MARKET" = spot ] && MKT='--instance-market-options MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}'
[ "${HW_DRY:-0}" = 1 ] && { echo "DRY RUN: would launch $TYPE $AMI_ID ($MARKET), cap $MAXMIN min"; exit 0; }
ID=$(aws ec2 run-instances --image-id "$AMI_ID" --instance-type $TYPE --key-name $KEY --security-group-ids "$SGID" \
      --instance-initiated-shutdown-behavior terminate --user-data "$UD" $MKT \
      --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=100,VolumeType=gp3,DeleteOnTermination=true}' \
      --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=trn-rtl-sim}]' --query 'Instances[0].InstanceId' --output text) || exit 1
echo "instance $ID launched $(date -Is)"; echo "$ID" > "$OUT/last_instance"
trap 'echo "terminating $ID $(date -Is)"; aws ec2 terminate-instances --instance-ids "$ID" --output text >/dev/null' EXIT
aws ec2 wait instance-running --instance-ids "$ID"
IP=$(aws ec2 describe-instances --instance-ids "$ID" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text); echo "ip $IP $(date -Is)"; echo "$IP" > "$OUT/last_ip"
SSH="ssh -i $PEM -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 ubuntu@$IP"
for i in $(seq 1 40); do $SSH true 2>/dev/null && break; sleep 10; done
echo "ssh up $(date -Is)"
$SSH 'mkdir -p ~/sa; ls /opt | grep -i venv; neuron-ls 2>/dev/null | head -8' 2>&1 | tee "$OUT/env.txt"
scp -i "$PEM" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null /opt/trn/sa/nki_sa.py /opt/trn/sa/nki_sa2.py /opt/trn/sa/nki_sa3.py /opt/trn/sa/nki_sa_hw.py /opt/trn/sa/nki_sa_hw2.py "ubuntu@$IP:~/sa/" >/dev/null
RUNLOG="$OUT/run_$TS.log"
$SSH 'VENV=$(ls -d /opt/aws_neuronx_venv_* 2>/dev/null | head -1); echo "venv $VENV"; source $VENV/bin/activate; pip list 2>/dev/null | grep -i -E "^(nki|neuronx-cc|nrtpy|numpy) "; cd ~/sa; export NEURON_LOGICAL_NC_CONFIG=1 NEURON_PLATFORM_TARGET_OVERRIDE=trn1 HW2_PROFILE=0 LD_LIBRARY_PATH=/opt/aws/neuron/lib:${LD_LIBRARY_PATH:-}; date -Is; pip install -q --extra-index-url https://pip.repos.neuron.amazonaws.com "nki==0.7.0+37713544100.gfede04bb" "neuronx-cc==2.28.7631.0+8f7b0cf5"; pip install -q --force-reinstall --no-deps --extra-index-url https://pip.repos.neuron.amazonaws.com "nrtpy==2.35.33+d2725ccb"; pip list 2>/dev/null | grep -i -E "^(nki|neuronx-cc|nrtpy) "; python -c "import nki.runtime; print(\"nki.runtime import ok\")"; export PATH=/opt/aws/neuron/bin:$PATH; date -Is; python nki_sa_hw2.py 128 256 5 --kernels sa,sa2,sa3 --out ~/sa/out; date -Is; python nki_sa_hw2.py 128 8192 5 --kernels sa3 --out ~/sa/out; date -Is; echo "=== profile pass"; PROF=$(command -v neuron-explorer || command -v neuron-profile); echo "profiler $PROF"; for n in ~/sa/out/*.neff; do b=${n%.neff}; echo "--- $n"; timeout 300 $PROF capture -n $n -s $b.ntff --ignore-exec-errors 2>&1 | tail -3; timeout 300 $PROF view -n $n -s $b.ntff --output-format summary-text 2>&1 | head -60; timeout 300 $PROF view -n $n -s $b.ntff --output-format summary-json > $b.summary.json 2>/dev/null; done; date -Is; cd ~/sa/out && tar czf ~/sa/hw2_artifacts.tgz --exclude=*.mlir --exclude=ir_dumps --exclude=*.colz . && ls -la ~/sa/hw2_artifacts.tgz; echo "=== remote done"' 2>&1 | tee "$RUNLOG"
scp -i "$PEM" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "ubuntu@$IP:~/sa/hw2_artifacts.tgz" "$OUT/hw2_artifacts_$TS.tgz" 2>/dev/null && echo "fetched hw2 artifacts into $OUT/hw2_artifacts_$TS.tgz"
if ! grep -q 'device mean=' "$RUNLOG" && [ "${HW_KEEP:-0}" = 1 ]; then
    echo "no device timing in $RUNLOG; HW_KEEP=1 -> leaving $ID ($IP) up until the $MAXMIN-min cap for debugging"
    trap - EXIT
fi
