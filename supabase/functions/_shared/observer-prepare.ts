import { DEFAULT_MODEL_ALIAS, encryptCredential } from "./observer-model.ts";
import type { Rpc } from "./observer-model.ts";
import { databaseLocator, GitHubError, placement } from "./observer-github.ts";
import type { GitHubApp } from "./observer-github.ts";
import { decodeTeamEgress, randomCapability } from "./observer-orchestrate.ts";

export async function schedulePreparations(deps: {
  rpc: Rpc;
  app: Pick<GitHubApp, "privateParticipantRepository" | "forkPublicSource">;
  masterKey: string;
  apiBase: string;
  limit?: number;
}) {
  const base = new URL(deps.apiBase);
  if (base.protocol !== "https:" || base.username || base.password || base.port || base.hash || base.search) {
    throw new GitHubError("invalid_platform_url");
  }
  const configs = await deps.rpc("observer_runner_configuration", {});
  if (!configs.length) return [];
  const enabled = new Set(configs.map((c: { organization: string }) => c.organization));
  const revisions = await deps.rpc("observer_pending_preparations", { p_limit: deps.limit ?? 3 });
  const outcomes = [];
  for (const revision of revisions) {
    try {
      const { organization, privateRepository } = await placement(revision.owner_id, databaseLocator(deps.rpc));
      if (!enabled.has(organization)) throw new GitHubError("runner_not_configured");
      const repository = await deps.app.privateParticipantRepository(revision.owner_id);
      if (repository.full_name !== organization + "/" + privateRepository) throw new GitHubError("invalid_repository");
      let source: Record<string, unknown>;
      // A repository submission stored at submission time (the pinned commit, or only
      // its chosen folder) is prepared from exactly that snapshot; older revisions
      // without one fork the public repository as before.
      const snapshot = revision.source_kind === "repository"
        ? await deps.rpc("observer_preparation_source", { p_revision: revision.id })
        : null;
      if (typeof snapshot === "string" && snapshot) {
        source = { archive_storage_ref: { bucket: "observer-sources", path: snapshot } };
      } else if (revision.source_kind === "repository") {
        const fork = await deps.app.forkPublicSource(
          revision.owner_id,
          revision.source_location,
          revision.submitted_commit ?? null,
        );
        source = { archive_ref: "github:" + fork.repository.full_name + "@" + fork.sourceCommit };
      } else if (revision.source_kind === "zip") {
        source = { archive_storage_ref: { bucket: "observer-staging", path: revision.source_location } };
      } else throw new GitHubError("invalid_source_kind");
      // Direct model access (switch observer_hardening.prepare_direct_model): the
      // adaptation calls the team's own provider with its variables, like an
      // evaluation; otherwise the platform model proxy as before.
      const team = await decodeTeamEgress(
        await deps.rpc("observer_preparation_team_egress", { p_revision: revision.id }).catch(() => null),
        deps.masterKey,
      );
      const id = crypto.randomUUID(), nonce = randomCapability();
      const participant = randomCapability(), engine = randomCapability();
      const input = {
        kind: "prepare",
        job_id: id,
        revision_id: revision.id,
        ...source,
        repository: { full_name: repository.full_name },
        artifact_upload: { kind: "github" },
        // Preparation always runs on the team's own model API (the online phase): ask for
        // the team's default model rather than a name the team's provider may not offer.
        ...(team ? { team_egress: team } : {
          model: DEFAULT_MODEL_ALIAS,
          model_base_url: base.href.replace(/\/$/, "") + "/functions/v1/observer-model/v1",
          run_credential: "obs_" + revision.model_run_id + "." + participant,
        }),
        // Only v4 public-test scenarios add this key; v3 preparation input is unchanged.
        ...(revision.gameplay === "v4" ? { gameplay: "v4" } : {}),
      };
      await deps.rpc("observer_schedule_preparation", {
        p_revision: revision.id,
        p_lease: revision.lease,
        p_organization: organization,
        p_participant_token: participant,
        p_engine_token: engine,
        p_job: {
          id,
          kind: "prepare",
          nonce,
          encrypted_input: await encryptCredential(JSON.stringify(input), id, deps.masterKey),
          encrypted_nonce: await encryptCredential(nonce, id + ":nonce", deps.masterKey),
        },
      });
      outcomes.push({ id: revision.id, scheduled: true });
    } catch (error) {
      const code = error instanceof GitHubError ? error.code : "preparation_unavailable";
      await deps.rpc("observer_preparation_error", { p_revision: revision.id, p_lease: revision.lease, p_error: code });
      outcomes.push({ id: revision.id, scheduled: false, error: code });
    }
  }
  return outcomes;
}
