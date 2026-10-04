// JSON API client. The token is handed to this page only (meta si-token, see app/server.py);
// every /api call carries it. Writes are same-origin POSTs, which the server's gate accepts.

const token = document.querySelector('meta[name="si-token"]')?.content || "";
const listeners = new Set();
let reachable = true;

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/** Called with true/false whenever the server becomes reachable or unreachable. */
export function onReachability(fn) { listeners.add(fn); }

function setReachable(value) {
  if (value !== reachable) {
    reachable = value;
    listeners.forEach(fn => fn(value));
  }
}

/** True if `name` was already set in this tab within the last minute; sets it otherwise. */
function sessionStorageFlag(name) {
  try {
    const last = Number(sessionStorage.getItem(name) || 0);
    if (Date.now() - last < 60000) return true;
    sessionStorage.setItem(name, String(Date.now()));
  } catch { /* storage blocked: allow the reload */ }
  return false;
}

async function request(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: { "X-Token": token, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch (err) {
    setReachable(false);
    throw new ApiError(0, String(err));
  }
  setReachable(true);
  if (response.status === 401 && !sessionStorageFlag("reloaded-for-token")) {
    // The server restarted and minted a new token: reload once to get it (served with the page).
    location.reload();
    return new Promise(() => {});
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, payload.error || response.statusText);
  return payload;
}

export const get = path => request("GET", path);
export const post = (path, body = {}) => request("POST", path, body);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/** POST something that starts a background job and wait for it (UAC prompts can take a while). */
export async function runJob(path, body = {}, { interval = 700, timeout = 300000 } = {}) {
  let job = await post(path, body);
  const started = Date.now();
  while (job.status === "running") {
    if (Date.now() - started > timeout) throw new ApiError(0, "timed out");
    await sleep(interval);
    job = await get(`/api/jobs/${job.id}`);
  }
  if (job.status === "error") throw new ApiError(500, job.error || "job failed");
  return job.result;
}
