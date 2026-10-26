#!/usr/bin/env bash
# Run after load has stopped and drained in the standalone sn-knee experiment.
set -e

k() {
  kubectl --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" -n sn-knee "$@"
}

# Clear generated documents without dropping collections or their indexes.
k exec deployment/post-storage-mongodb -- mongo --quiet post --eval \
  'printjson(db.getCollection("post").deleteMany({}));'
k exec deployment/user-timeline-mongodb -- mongo --quiet user-timeline --eval \
  'printjson(db.getCollection("user-timeline").deleteMany({}));'
k exec deployment/url-shorten-mongodb -- mongo --quiet url-shorten --eval \
  'printjson(db.getCollection("url-shorten").deleteMany({}));'

# Clear generated timelines; keep the initialized social graph and username cache.
for service in home-timeline-redis user-timeline-redis; do
  k exec "deployment/$service" -- redis-cli FLUSHDB SYNC
done

echo 'Generated data reset. Users, graph, indexes, and username cache preserved.'
