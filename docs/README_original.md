For testing this workflow on the existing cluster with separate namespaces, use
[the README test walkthrough](../README.md). It follows the same sections,
charts, release names, and dataset, with separate databases.

# Current Kubernetes Setup
These are the likely setup commands I executed as I remember them, but are not verified. The current cluster state is the state I used for the demo.
Skip to the next section `Demo Commands` if the cluster is all set up, which it currently is.

## Apply the DeathStarBench changes
I made some changes to DeathStarBench (these are in the patches directory)

From the root of this repository, after cloning with `--recurse-submodules`:

```bash
git -C DeathStarBench apply --check "$PWD/patches/deathstarbench.patch" &&
git -C DeathStarBench apply "$PWD/patches/deathstarbench.patch"
```

The patch adds external Redis/MongoDB configuration. The chart changes are used during Helm installation. The C++ fix additionally requires building and deploying an image containing the modified service

## Monitoring and autoscalers

```bash
helm install kps kube-prometheus-stack \
  --repo https://prometheus-community.github.io/helm-charts --version 88.2.0 \
  -n monitoring --create-namespace -f manifests/kps-values.yaml \
  --wait --timeout 20m

# installing Foxtrot operator
kubectl apply -f redis-foxtrot-autoscaler/operator.yaml

# installing Mambo operator
kubectl apply -f Autoscaling/MongoDBOperator/dist/install.yaml

# applying limits to foxtrot
kubectl apply -f manifests/foxtrot-namespace.yaml

# similar to Satyam's redis-cluster.yaml file
kubectl apply -f manifests/redis-cluster-cloudlab.yaml
```

## MongoDB and Mambo policy

```bash
helm install sn-mongodb-sharded mongodb-sharded \
  --repo https://charts.bitnami.com/bitnami --version 9.4.12 \
  -n mambo-socialnetwork --create-namespace \
  -f Autoscaling/MongoDB/values.yaml --wait --timeout 15m

# Initialize post.post, its indexes, and hashed post_id sharding; inserts no posts.
kubectl -n mambo-socialnetwork exec -i deployment/sn-mongodb-sharded-mongos -- bash -ec '
  exec /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
    --username root --password "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
    --authenticationDatabase admin --quiet --file /dev/stdin
' < manifests/init-sanity-mongodb.js

helm install sn-sanity-node-exporter prometheus-node-exporter \
  --repo https://prometheus-community.github.io/helm-charts --version 4.56.1 \
  -n mambo-socialnetwork \
  --set prometheus.monitor.enabled=true \
  --set prometheus.monitor.additionalLabels.release=kps \
  --wait --timeout 5m

kubectl apply -f manifests/mongodautoscaler-sanity.yaml
```

## SocialNetwork

```bash
helm install sn-sanity DeathStarBench/socialNetwork/helm-chart/socialnetwork \
  -n social-network --create-namespace \
  -f manifests/socialnetwork-foxtrot-values.yaml \
  -f manifests/socialnetwork-sanity-values.yaml \
  --set-string jaeger.container.imageVersion=1.62.0 \
  --wait --timeout 15m

# Use the public image containing the home-timeline Redis redirect fix.
kubectl -n social-network set image deployment/home-timeline-service \
  home-timeline-service=docker.io/adas2125/social-network-microservices@sha256:d96573d37b71105f26897bf55c3379cafcaf6630858de5bd2a6c25a0511a1f72
```

## Dataset initialization

Run the port-forward in the background so you can use the same Bash terminal:

```bash
kubectl -n social-network port-forward service/nginx-thrift 18080:8080 \
  > /tmp/socialnetwork-port-forward.log 2>&1 &
SOCIALNETWORK_PORT_FORWARD_PID=$!
```

Check the log; wait until it shows `Forwarding from` before running the initializer:

```bash
cat /tmp/socialnetwork-port-forward.log
```

```bash
python3 -m venv .venv
.venv/bin/pip install aiohttp
(cd DeathStarBench/socialNetwork && ../../.venv/bin/python scripts/init_social_graph.py \
  --graph=socfb-Reed98 --ip=127.0.0.1 --port=18080 --limit=50 --compose)
```

After initialization finishes, stop the port-forward in the same terminal:

```bash
kill "$SOCIALNETWORK_PORT_FORWARD_PID"
```

## Load-generator pod

```bash
kubectl apply -f manifests/wrk2-pod.yaml
```

# Demo Commands
## Define the alias
Run this setup in each Bash terminal, replacing the filename and context with your own:

```bash
export KUBECONFIG="$HOME/.kube/your-kubeconfig"
export KUBE_CONTEXT="YOUR_CONTEXT"
alias k='kubectl --context "$KUBE_CONTEXT"'
```

## See the CPU usage of pods associated with redis, mongodb, and social-network (keep open in another tab)
```bash
watch -n 2 'kubectl \
  --kubeconfig "$KUBECONFIG" \
  --context "$KUBE_CONTEXT" \
  top pods -A --sort-by=cpu |
  rg "^NAMESPACE|^social-network[[:space:]]|[[:space:]](redis-cluster-|sn-mongodb-sharded-shard)"'
```

## Review the Autoscaler Logs (keep open in another tab)
```bash
# logs from FoxTrot
k logs -n redis-operator-system deployment/redis-operator-controller-manager --tail=10 -f

# logs from Mambo
k logs -n mongodboperator-system deployment/mongodboperator-controller-manager --tail=10 -f
```

## See the current settings for the operator
```bash
# For FoxTrot
k get rediscluster redis-cluster -n foxtrot -o json \
  | jq '{spec, status}'

# For Mambo
k get mongodautoscaler socialnetwork-sanity -n mambo-socialnetwork -o json \
  | jq '{spec, status}'
```

## Patching Mambo to enable autoscaling
```bash
# this eables autoscaling for Mambo. To disable (minShards = maxShards) or enforce downscaling (maxShards to 1)
kubectl \
  --kubeconfig="$KUBECONFIG" \
  --context="$KUBE_CONTEXT" \
  -n mambo-socialnetwork patch mongodautoscaler socialnetwork-sanity \
  --type=merge -p '{"spec":{"shardScaleBounds":{"maxShards":2}}}'

# cooldownSeconds controls how long it takes for the next autoscaling decision to be made
kubectl \
  --kubeconfig="$KUBECONFIG" \
  --context="$KUBE_CONTEXT" \
  -n mambo-socialnetwork patch mongodautoscaler socialnetwork-sanity \
  --type=merge -p '{"spec":{"policy":{"cooldownSeconds":60}}}'
```

## Patching FoxTrot to enable autoscaling
```bash
# enable autoscaling and set the thresholds to low so we can see it in effect
kubectl \
  --kubeconfig="$KUBECONFIG" \
  --context="$KUBE_CONTEXT" \
  -n foxtrot patch rediscluster redis-cluster --type=merge \
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


## Run the compose-post workload after autoscaling enabled (keep open in another tab)
```bash
# open up the shell
kubectl \
  --kubeconfig "$KUBECONFIG" \
  --context "$KUBE_CONTEXT" \
  -n social-network exec -it sn-wrk2 -c wrk2 -- bash

# Run the workload
cd /work/DeathStarBench/socialNetwork
app_ip="http://nginx-thrift.social-network.svc.cluster.local:8080"
../wrk2/wrk -D exp -t 8 -c 100 -d 6000s -L \
  -s ./wrk2/scripts/social-network/compose-post.lua \
  "$app_ip" -R 300
```

## Inspecting Mambo w/ Mongo Shell (during autoscaling, open in another terminal)
```bash
# starting the shell
k -n mambo-socialnetwork exec -it deployment/sn-mongodb-sharded-mongos   -c mongos -- sh -c '
  exec /opt/bitnami/mongodb/bin/mongosh \
    --host 127.0.0.1 --port 27017 \
    --username "$MONGODB_ROOT_USER" \
    --password "$(cat "$MONGODB_ROOT_PASSWORD_FILE")" \
    --authenticationDatabase admin --quiet --norc
'

# Checking balancer status (active in autoscaling)
db.adminCommand({balancerStatus: 1})

# Seeing the number of documents owned by each shard
db.getSiblingDB("admin").aggregate([
  { $shardedDataDistribution: {} },
  { $match: { ns: "post.post" } }
]).toArray()
```

## Inspecting Foxtrot
```bash
# Seeing the FoxTrot keys distribution across masters
k -n foxtrot exec redis-cluster-0 -c redis -- \
  redis-cli --cluster info 127.0.0.1:6379
```
