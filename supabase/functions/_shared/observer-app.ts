import { databaseLocator, GitHubApp, GitHubError, RUNNER_ORGANIZATION_PATTERN } from "./observer-github.ts";
import type { SupabaseClient } from "npm:@supabase/supabase-js@2";

export async function configuredApp(service: SupabaseClient): Promise<GitHubApp> {
  const { data, error } = await service.rpc("observer_runner_configuration");
  if (error || !Array.isArray(data)) throw new GitHubError("github_configuration_unavailable");
  const installations = Object.fromEntries(data.map((c) => [c.organization, c.installation_id]));
  return new GitHubApp(
    Deno.env.get("OBSERVER_GITHUB_APP_ID") ?? "",
    Deno.env.get("OBSERVER_GITHUB_APP_PEM") ?? "",
    installations,
    databaseLocator(async (name, args) => {
      const { data, error } = await service.rpc(name, args);
      if (error) throw new GitHubError("placement_unavailable");
      return data;
    }),
  );
}

const ARCHIVE_REFERENCE = new RegExp(
  "^github:(" + RUNNER_ORGANIZATION_PATTERN + ")\\/(participant-[0-9a-f]{32}|source-[0-9a-f]{20})@([0-9a-f]{40})$",
);

export function archiveReference(value: unknown, privateOnly = true) {
  if (typeof value !== "string") throw new GitHubError("invalid_artifact_reference");
  const match = ARCHIVE_REFERENCE.exec(value);
  if (!match || (privateOnly && !match[2].startsWith("participant-"))) {
    throw new GitHubError("invalid_artifact_reference");
  }
  return { organization: match[1], repository: match[2], commit: match[3] };
}

export async function artifactDownload(app: GitHubApp, reference: string, privateOnly = true) {
  const ref = archiveReference(reference, privateOnly);
  const repo = await app.repository(ref.organization, ref.repository);
  if (privateOnly && (!repo.private || repo.fork)) throw new GitHubError("private_artifact_required");
  return await app.archiveDownload(ref.organization, ref.repository, ref.commit);
}
