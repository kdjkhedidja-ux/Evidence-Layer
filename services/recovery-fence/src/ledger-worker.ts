import type { Env } from "./types";
import { LedgerState } from "./ledger";

export { LedgerState };

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (!env.LEDGER_STATE) {
      return new Response(JSON.stringify({ error: "ledger unavailable" }), {
        status: 503,
        headers: { "content-type": "application/json", "cache-control": "no-store" },
      });
    }
    const id = env.LEDGER_STATE.idFromName("evidence-recovery-ledger-v1");
    return env.LEDGER_STATE.get(id).fetch(request);
  },
};
