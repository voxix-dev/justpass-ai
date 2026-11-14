# Source before any Substrate/kind command. kubectl uses its current context: point it at the kind cluster.
# Linux hosts: if Envoy crashes on inotify, raise fs.inotify.max_user_instances=1024 and max_user_watches=524288.
# Clients the atenet router accepts: Substrate's e2e client and our API.
export ATE_INGRESS_ALLOWED_SPIFFE_IDS=spiffe://cluster.local/ns/ate-e2e-router-client/sa/router-client,spiffe://cluster.local/ns/homework/sa/api
# Local ports of the port-forwards (forward.sh), which the API uses (compose.yaml here).
export ATE_API_PORT=${ATE_API_PORT:-8443} ATE_ROUTER_PORT=${ATE_ROUTER_PORT:-9443}
