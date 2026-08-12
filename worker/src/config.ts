export const config = {
  apiUrl: process.env.AUTOWPP_API_URL ?? "http://localhost:8000/api/v1",
  workerToken: process.env.AUTOWPP_WORKER_TOKEN ?? "change-worker-token",
  workerId: process.env.AUTOWPP_WORKER_ID ?? `worker-${process.pid}`,
  nodeId: process.env.AUTOWPP_NODE_ID ?? "node-local",
  capacity: Number.parseInt(process.env.AUTOWPP_WORKER_CAPACITY ?? "15", 10),
  heartbeatMs: Number.parseInt(process.env.AUTOWPP_HEARTBEAT_MS ?? "10000", 10),
  claimAccountsMs: Number.parseInt(process.env.AUTOWPP_CLAIM_ACCOUNTS_MS ?? "5000", 10),
  jobPollMs: Number.parseInt(process.env.AUTOWPP_JOB_POLL_MS ?? "5000", 10),
};
