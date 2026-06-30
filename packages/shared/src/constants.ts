// Gateway and dashboard ports — must match config.yml defaults
export const GATEWAY_PORT = 47821
export const DASHBOARD_PORT = 47822
export const GATEWAY_BASE_URL = `http://localhost:${GATEWAY_PORT}`

// .agentos directory layout
export const AGENTOS_DIR = '.agentos'
export const CONFIG_FILE = `${AGENTOS_DIR}/config.yml`
export const AGENTS_FILE = `${AGENTOS_DIR}/agents.yml`
export const POLICIES_FILE = `${AGENTOS_DIR}/policies.yml`
export const CONTEXT_DIR = `${AGENTOS_DIR}/context`
export const MISSIONS_DIR = `${AGENTOS_DIR}/missions`
export const RUNS_DIR = `${AGENTOS_DIR}/runs`
export const ARTIFACTS_DIR = `${AGENTOS_DIR}/artifacts`
export const APPROVALS_DIR = `${AGENTOS_DIR}/approvals`
export const SECRETS_DIR = `${AGENTOS_DIR}/.secrets`
export const WORKSPACES_DIR = `${AGENTOS_DIR}/workspaces`

// Risk score thresholds (must match policies.yml defaults)
export const RISK_AUTO_APPROVE = 30
export const RISK_ASK_USER = 70
export const RISK_BLOCK = 100

// Context pack limits
export const CONTEXT_TOKEN_BUDGET_DEFAULT = 8000
export const CONTEXT_TOKEN_BUDGET_MAX = 16000

// Credential monitoring
export const CREDENTIAL_PROBE_INTERVAL_MS = 2000
export const CREDENTIAL_EXPIRY_WARNING_DAYS = 3

// Retry policy for rate-limited agents
export const RATE_LIMIT_RETRY_DELAYS_MS = [1000, 2000, 4000] // 3 retries, exponential
