# Sourced by deploy/local/deploy.sh. The one place that sizes the agent: the sandbox (agent-template.yaml)
# must fit inside its worker pod (agent-pool.yaml), so both are derived from these values.
AGENT_CPU_MILLICORES=1000
AGENT_MEMORY_MIB=512
WORKER_OVERHEAD_MIB=512 # gVisor and the worker itself, on top of the sandbox
WORKER_CPU_REQUEST_MILLICORES=250

AGENT_MEMORY_BYTES=$((AGENT_MEMORY_MIB * 1024 * 1024))
WORKER_MEMORY_MIB=$((AGENT_MEMORY_MIB + WORKER_OVERHEAD_MIB))
export AGENT_CPU_MILLICORES AGENT_MEMORY_BYTES WORKER_CPU_REQUEST_MILLICORES WORKER_MEMORY_MIB
