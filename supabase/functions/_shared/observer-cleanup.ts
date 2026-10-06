import type { Rpc } from "./observer-model.ts";

/** Only temporary uploads selected by the database; no repository/history deletion. */
export async function cleanupUploads(rpc: Rpc, remove: (path: string) => Promise<void>) {
  const uploads = await rpc("observer_expired_uploads", { p_limit: 50 });
  let cleaned = 0;
  for (const upload of uploads) {
    if (!/^[0-9a-f-]{36}\/[0-9a-f-]{36}\/(source\.zip|decisions\.csv)$/.test(upload.path)) continue;
    try {
      await remove(upload.path);
      await rpc("observer_upload_cleaned", { p_upload: upload.id });
      cleaned++;
    } catch {
      // Leave its durable record eligible for the next tick. Never print URLs.
    }
  }
  return cleaned;
}

/**
 * Sealed public-pool transfer objects (ciphertext only) of finished jobs:
 * sealed/<job>/{scenario,project,result,trace}.zip in the staging bucket.
 */
export async function cleanupSealed(rpc: Rpc, remove: (paths: string[]) => Promise<void>) {
  const jobs: string[] = await rpc("observer_public_sealed_pending", { p_limit: 20 });
  let cleaned = 0;
  for (const job of jobs) {
    if (!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(job)) continue;
    try {
      await remove(["scenario", "project", "result", "trace"].map((name) => "sealed/" + job + "/" + name + ".zip"));
      await rpc("observer_public_sealed_cleaned", { p_job: job });
      cleaned++;
    } catch {
      // Retried on the next tick.
    }
  }
  return cleaned;
}
