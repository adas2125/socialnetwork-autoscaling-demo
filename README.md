# Revising SocialNetwork, FoxTrot, Mambo Deployment

[Previous demo instructions](docs/README_original.md)

This file follows a similar setup, but creates everything in new namespaces, which are deleted after the demo is over.

| Original namespace | Test namespace |
| --- | --- |
| `social-network` | `readme-test-app` |
| `foxtrot` | `readme-test-redis` |
| `mambo-socialnetwork` | `readme-test-mongo` |

Reuse the installed monitoring, node exporter, and operators. Installation docs:
[monitoring](docs/README_original.md#monitoring-and-autoscalers),
[node exporter](docs/README_original.md#mongodb-and-mambo-policy),
[FoxTrot](https://github.com/SatyamS17/redis-foxtrot-autoscaler/blob/6e85a07a2fc4434d1e7f3b2cc74a85e641242668/README.md#installation), and
[Mambo](https://github.com/Chen-Si-An/Autoscaling/blob/68314fe664c4a82883ee286d192c77f273525569/README.md#autoscaler-operator-setup).

## Set up the test namespaces

Run from the root (i.e. `~/socialnetwork-autoscaling-demo`).

```bash
export KUBECONFIG="$HOME/.kube/your-kubeconfig"
export KUBE_CONTEXT="YOUR_CONTEXT"
alias k='kubectl --context "$KUBE_CONTEXT"'
```

Create the test namespaces:

```bash
k top nodes
k apply -f manifests/readme-test/bootstrap.yaml
k get namespaces readme-test-app readme-test-redis readme-test-mongo
```

Bootstrap creates the three namespaces and copies the Redis LimitRange from
[manifests/foxtrot-namespace.yaml](manifests/foxtrot-namespace.yaml).

# Current Kubernetes Setup

## Apply the DeathStarBench changes

Apply the patch once; this skips it if already applied.

```bash
if git -C DeathStarBench apply --reverse --check "$PWD/patches/deathstarbench.patch" 2>/dev/null; then
  echo "DeathStarBench patch is already applied"
else
  git -C DeathStarBench apply --check "$PWD/patches/deathstarbench.patch" &&
    git -C DeathStarBench apply "$PWD/patches/deathstarbench.patch"
fi
```

The [patch](patches/deathstarbench.patch) changes both the chart and C++ source:

- **Helm chart:** adds external Redis and MongoDB configuration for the selected services.
- **Home-timeline C++:** catches Redis routing redirects during FoxTrot resharding
  and retries writes using cluster-aware commands. In the original implementation, whenever 
  FoxTrot resharded data, I was receiving internal service errors. This patch should solve 
  that. New docker image pull instructions for the home-timeline-service are below.

## Monitoring and autoscalers
Use the existing monitoring and operators. Reuse
[redis-cluster-cloudlab.yaml](manifests/redis-cluster-cloudlab.yaml), changing
only `metadata.namespace` to `readme-test-redis`:

```bash
sed 's/namespace: foxtrot$/namespace: readme-test-redis/' \
  manifests/redis-cluster-cloudlab.yaml | k apply -f -
k wait -n readme-test-redis --for=jsonpath='{.status.initialized}'=true \
  rediscluster/redis-cluster --timeout=15m
k get pods,jobs -n readme-test-redis
```

## MongoDB and Mambo policy

Install the same MongoDB chart and values in the test namespace:

```bash
helm --kube-context "$KUBE_CONTEXT" install sn-mongodb-sharded mongodb-sharded \
  --repo https://charts.bitnami.com/bitnami --version 9.4.12 \
  -n readme-test-mongo \
  -f Autoscaling/MongoDB/values.yaml --wait --timeout 15m

# Initialize post.post, its indexes, and hashed post_id sharding; inserts no posts.
k -n readme-test-mongo exec -i deployment/sn-mongodb-sharded-mongos -- bash -ec '
  exec /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
    --username root --password "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
    --authenticationDatabase admin --quiet --file /dev/stdin
' < manifests/init-sanity-mongodb.js

# Change metadata.namespace and spec.bitnami.namespace in the original Mambo policy.
sed 's/namespace: mambo-socialnetwork$/namespace: readme-test-mongo/' \
  manifests/mongodautoscaler-sanity.yaml | k apply -f -
```

## SocialNetwork
The patched home-timeline image is now public.

```bash
helm --kube-context "$KUBE_CONTEXT" install sn-sanity DeathStarBench/socialNetwork/helm-chart/socialnetwork \
  -n readme-test-app \
  -f manifests/socialnetwork-foxtrot-values.yaml \
  -f manifests/socialnetwork-sanity-values.yaml \
  -f manifests/readme-test/socialnetwork-values.yaml \
  --set-string jaeger.container.imageVersion=1.62.0 \
  --wait --timeout 15m

k -n readme-test-app set image deployment/home-timeline-service \
  home-timeline-service=docker.io/adas2125/social-network-microservices@sha256:d96573d37b71105f26897bf55c3379cafcaf6630858de5bd2a6c25a0511a1f72
k -n readme-test-app rollout status deployment/home-timeline-service --timeout=5m
```

## Dataset initialization

Non-blocking post forward (killed later):

```bash
k -n readme-test-app port-forward service/nginx-thrift 18081:8080 \
  > /tmp/readme-test-port-forward.log 2>&1 &
README_TEST_PORT_FORWARD_PID=$!
```

Run the original dataset initializer against that port.

```bash
python3 -m venv .venv
.venv/bin/pip install aiohttp
(cd DeathStarBench/socialNetwork && ../../.venv/bin/python scripts/init_social_graph.py \
  --graph=socfb-Reed98 --ip=127.0.0.1 --port=18081 --limit=50 --compose)
```

Stop the port-forward in the same terminal:

```bash
kill "$README_TEST_PORT_FORWARD_PID"
```

## Load-generator pod

Reuse [wrk2-pod.yaml](manifests/wrk2-pod.yaml), changing `metadata.namespace`
to `readme-test-app`.

To optionally select a node for wrk2, list the nodes and database pod locations:
```bash
k get nodes -L kubernetes.io/hostname,kubernetes.io/arch
k -n readme-test-redis get pods -o wide
k -n readme-test-mongo get pods -o wide
```

Choose one without Redis or MongoDB pods. In `manifests/wrk2-pod.yaml`,
uncomment the hostname line under `nodeSelector` and replace `YOUR_NODE_HOSTNAME`
with that node's `kubernetes.io/hostname` label value from the listing:

```yaml
  nodeSelector:
    kubernetes.io/arch: amd64
    kubernetes.io/hostname: YOUR_NODE_HOSTNAME
```

Make this optional edit before creating the pod:

```bash
sed 's/namespace: social-network$/namespace: readme-test-app/' \
  manifests/wrk2-pod.yaml | k apply -f -
k wait -n readme-test-app --for=condition=Ready pod/sn-wrk2 --timeout=15m
k -n readme-test-app get pod sn-wrk2 -o wide
```

# Demo Commands

## See CPU usage

Keep open in another terminal:

```bash
watch -n 2 'kubectl --context "$KUBE_CONTEXT" top pods -A --sort-by=cpu |
  rg "^NAMESPACE|^readme-test-(app|redis|mongo)[[:space:]]"'
```

## Review the autoscaler logs

Run each command in a separate terminal.

```bash
k logs -n redis-operator-system deployment/redis-operator-controller-manager --tail=10 -f
```

```bash
k logs -n mongodboperator-system deployment/mongodboperator-controller-manager --tail=10 -f
```

## See the current operator settings

```bash
k get rediscluster redis-cluster -n readme-test-redis -o json | jq '{spec, status}'
k get mongodautoscaler socialnetwork-sanity -n readme-test-mongo -o json | jq '{spec, status}'
```

## Patching Mambo to enable autoscaling

Allow up to two shards and set a 60-second cooldown:

```bash
k -n readme-test-mongo patch mongodautoscaler socialnetwork-sanity \
  --type=merge -p '{"spec":{"shardScaleBounds":{"maxShards":2}}}'
k -n readme-test-mongo patch mongodautoscaler socialnetwork-sanity \
  --type=merge -p '{"spec":{"policy":{"cooldownSeconds":60}}}'
```

## Patching FoxTrot to enable autoscaling

```bash
k -n readme-test-redis patch rediscluster redis-cluster --type=merge \
  -p '{"spec":{
    "autoScaleEnabled":true,
    "minMasters":3,
    "cpuThreshold":10,
    "cpuThresholdLow":5,
    "memoryThreshold":70,
    "memoryThresholdLow":40,
    "scaleCooldownSeconds":60
  }}'
```

## Run the compose-post workload

Start a load test, monitor CPU usage to determine what rate you should run at.

```bash
k -n readme-test-app exec -it sn-wrk2 -c wrk2 -- bash
```

Inside the pod:

```bash
cd /work/DeathStarBench/socialNetwork
app_ip="http://nginx-thrift.readme-test-app.svc.cluster.local:8080"
../wrk2/wrk -D exp -t 8 -c 100 -d 180s -L \
  -s ./wrk2/scripts/social-network/compose-post.lua "$app_ip" -R 300
```

Some issues to be on the lookout for:
  - OOM issues (compose-post writes)

## Inspecting Mambo with Mongo shell

Open the shell in another terminal:

```bash
k -n readme-test-mongo exec -it deployment/sn-mongodb-sharded-mongos -c mongos -- sh -c '
  exec /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
    --username "$MONGODB_ROOT_USER" --password "$(cat "$MONGODB_ROOT_PASSWORD_FILE")" \
    --authenticationDatabase admin --quiet --norc
'
```

Run these in `mongosh`:

```javascript
db.adminCommand({listShards: 1})
db.adminCommand({balancerStatus: 1})
db.getSiblingDB("admin").aggregate([
  { $shardedDataDistribution: {} },
  { $match: { ns: "post.post" } }
]).toArray()
```

## Inspecting FoxTrot

```bash
k -n readme-test-redis exec redis-cluster-0 -c redis -- \
  redis-cli --cluster info 127.0.0.1:6379
```

## Cleanup

When finished, delete the test workloads and their PVCs:

```bash
k delete namespace readme-test-app readme-test-redis readme-test-mongo
```
