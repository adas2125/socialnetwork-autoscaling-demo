# SocialNetwork: standalone knee experiment
This demo uses standalone Redis and MongoDB with fixed application replicas and no CPU or
memory limits. We first inspect CPU when a compose-post workload is run at **1.2K** RPS. We then run a latency-only sweep at **150, 300, 600, and 1.2K RPS**. For this demo, we use three terminals: main commands, CPU tracking, and port-forwarding during initialization.

## 1. Terminal setup
```bash
cd ~/socialnetwork-autoscaling-demo
export KUBECONFIG="$HOME/.kube/amit.kubeconfig"
export KUBE_CONTEXT="amit@chirag-m5a-large-0803-215701.k8s.local"
alias k='kubectl --context "$KUBE_CONTEXT"'
export SN_RESULTS="$PWD/results/sn-knee/standalone-sweep/run-1"
mkdir -p "$SN_RESULTS"
set -o pipefail
```

```bash
git submodule update --init DeathStarBench
if git -C DeathStarBench apply --reverse --check \
  "$PWD/patches/deathstarbench-knee-chart.patch" 2>/dev/null; then
  echo "Knee chart patch is already applied"
else
  git -C DeathStarBench apply --check \
    "$PWD/patches/deathstarbench-knee-chart.patch" &&
  git -C DeathStarBench apply "$PWD/patches/deathstarbench-knee-chart.patch"
fi
```

## 2. Deploy with fixed placement
Use [baseline-values](../manifests/socialnetwork-knee-baseline-values.yaml).
It keeps the tested replicas and placements, uses image tags, and inherits the
other chart defaults.

| Component | Replicas | Fixed worker |
| --- | ---: | --- |
| nginx-thrift | 12 | Automatically scheduled |
| compose-post-service | 2 | `i-02e0006573b905dca` (4 CPUs) |
| post-storage-service | 5 | Automatically scheduled |
| home-timeline-service | 2 | Automatically scheduled |
| user-timeline-service | 2 | Automatically scheduled |
| user-timeline-mongodb | 1 | `i-091443c764376d8ba` (4 CPUs) |
| text-service | 1 | `i-01a5857cf7598b7f8` |
| wrk2 | 1 | `i-0188da2a87c4380bc` |
| Other deployments | 1 each | Automatically scheduled |

For a fresh deployment, `sn-knee` should be absent:

```bash
k get namespace sn-knee

# deploying SocialNetwork
k create namespace sn-knee
helm --kube-context "$KUBE_CONTEXT" install sn-knee \
  DeathStarBench/socialNetwork/helm-chart/socialnetwork -n sn-knee \
  -f manifests/socialnetwork-knee-baseline-values.yaml \
  --wait --timeout 15m

# wait for deployments to finish starting
k -n sn-knee rollout status deployment --timeout=10m

# read-only commands
k -n sn-knee get deploy
k -n sn-knee get pods -o wide
k -n sn-knee get hpa
k -n sn-knee get configmap home-timeline-service \
  -o jsonpath='{.data.service-config\.json}' | jq '."home-timeline-redis"'
```

## 3. Initialize Reed98 and wrk2

In the **port-forward terminal**:

```bash
export KUBECONFIG="$HOME/.kube/amit.kubeconfig"
export KUBE_CONTEXT="amit@chirag-m5a-large-0803-215701.k8s.local"
kubectl --context "$KUBE_CONTEXT" -n sn-knee \
  port-forward service/nginx-thrift 18082:8080
```

In the **main terminal**:

```bash
curl -I http://127.0.0.1:18082/
python3 -m venv .venv
.venv/bin/pip install aiohttp
(cd DeathStarBench/socialNetwork && ../../.venv/bin/python scripts/init_social_graph.py \
  --graph=socfb-Reed98 --ip=127.0.0.1 --port=18082 --limit=50)
```

Reuse [wrk2-pod.yaml](../manifests/wrk2-pod.yaml) with minimal overrides.

```bash
kubectl patch --local -f manifests/wrk2-pod.yaml --type=strategic -p '{
  "metadata": {"namespace": "sn-knee"},
  "spec": {
    "nodeSelector": {"kubernetes.io/hostname": "i-0188da2a87c4380bc"},
    "containers": [{
      "name": "wrk2",
      "resources": {"requests": {"cpu": "500m"}}
    }]
  }
}' -o yaml | k apply -f -
k -n sn-knee wait --for=jsonpath='{.status.phase}'=Running \
  pod/sn-wrk2 --timeout=5m
k -n sn-knee logs -f sn-wrk2 -c wrk2
```

At `READY: wrk2 built`, press Ctrl+C, then run:

```bash
k -n sn-knee wait --for=condition=Ready pod/sn-wrk2 --timeout=15m
k -n sn-knee cp \
  DeathStarBench/socialNetwork/wrk2/scripts/social-network/compose-post.lua \
  sn-wrk2:/work/compose-post.lua -c wrk2
```

## 4. Track CPU at 1,200 RPS
Run from the **host main terminal**.
The [reset command](../scripts/reset-sn-knee-generated.sh) clears generated MongoDB
posts, user timelines, and URLs, plus the two timeline Redis databases. It preserves
users, the graph, indexes, and `user-memcached`. 

With all load stopped and drained:

```bash
bash scripts/reset-sn-knee-generated.sh
k -n sn-knee exec sn-wrk2 -c wrk2 -- env max_user_index=962 \
  /work/DeathStarBench/wrk2/wrk -D exp -t 4 -c 40 -T 10s -d 30s -L \
  -s /work/compose-post.lua \
  http://nginx-thrift.sn-knee.svc.cluster.local:8080 -R 100 \
  2>&1 | tee "$SN_RESULTS/warmup-cpu-1200.txt"
```

In the **CPU tracking terminal**:

```bash
cd ~/socialnetwork-autoscaling-demo
export KUBECONFIG="$HOME/.kube/amit.kubeconfig"
export KUBE_CONTEXT="amit@chirag-m5a-large-0803-215701.k8s.local"
alias k='kubectl --context "$KUBE_CONTEXT"'
export SN_RESULTS="$PWD/results/sn-knee/standalone-sweep/run-1"
mkdir -p "$SN_RESULTS/cpu-1200"

while true; do
  date -u '+%Y-%m-%dT%H:%M:%SZ'
  k -n sn-knee top pods --sort-by=cpu
  k top nodes
  sleep 5
done | tee "$SN_RESULTS/cpu-1200/cpu.txt"
```

Back in the **main terminal**, run:

```bash
k -n sn-knee exec sn-wrk2 -c wrk2 -- env max_user_index=962 \
  /work/DeathStarBench/wrk2/wrk -D exp -t 4 -c 400 -T 10s -d 60s -L \
  -s /work/compose-post.lua \
  http://nginx-thrift.sn-knee.svc.cluster.local:8080 -R 1200 \
  2>&1 | tee "$SN_RESULTS/cpu-1200/wrk2.txt"
```

Stop tracking with Ctrl+C.

## 5. Latency sweep: 150, 300, 600, 1,200 RPS

Run this in the **host main terminal**.

```bash
mkdir -p "$SN_RESULTS/sweep"
set -o pipefail
for rate in 150 300 600 1200; do
  bash scripts/reset-sn-knee-generated.sh || break

  k -n sn-knee exec sn-wrk2 -c wrk2 -- env max_user_index=962 \
    /work/DeathStarBench/wrk2/wrk -D exp -t 4 -c 40 -T 10s -d 30s -L \
    -s /work/compose-post.lua \
    http://nginx-thrift.sn-knee.svc.cluster.local:8080 -R 100 \
    2>&1 | tee "$SN_RESULTS/sweep/warmup-${rate}.txt" || break

  k -n sn-knee exec sn-wrk2 -c wrk2 -- env max_user_index=962 \
    /work/DeathStarBench/wrk2/wrk -D exp -t 4 -c 400 -T 10s -d 60s -L \
    -s /work/compose-post.lua \
    http://nginx-thrift.sn-knee.svc.cluster.local:8080 -R "$rate" \
    2>&1 | tee "$SN_RESULTS/sweep/rate-${rate}.txt" || break
done
```

Plot offered RPS against mean, p50, and p99 latency from `sweep/rate-*.txt` to discover the knee.

## 6. Cleanup
```bash
k delete namespace sn-knee
k wait --for=delete namespace/sn-knee --timeout=5m
```
