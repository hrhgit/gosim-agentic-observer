import { assertEquals } from "@std/assert";
import { decryptCredential, encryptCredential } from "./observer-model.ts";
import { placement } from "./observer-github.ts";

// Recorded placement from public.observer_placement; runner-9 is one of the added organizations.
const locate = () => Promise.resolve("AGENTIC-OBSERVER26-runner-9");
import { schedulePreparations } from "./observer-prepare.ts";

const user = "00000000-0000-4000-8000-000000000001";
const revision = "00000000-0000-4000-8000-000000000002";
const modelRun = "00000000-0000-4000-8000-000000000003";
const lease = "00000000-0000-4000-8000-000000000004";
const key = btoa("k".repeat(32));

for (const source_kind of ["repository", "zip"]) {
  Deno.test(
    source_kind + " preparation snapshots source and scopes the model budget without installation secrets",
    async () => {
      const { organization, privateRepository } = await placement(user, locate);
      const privateRepo = {
        id: 42,
        full_name: organization + "/" + privateRepository,
        private: true,
        fork: false,
        default_branch: "main",
      };
      let scheduled: Record<string, any> = {};
      let forks = 0;
      const source_location = source_kind === "repository"
        ? "https://github.com/example/project"
        : user + "/" + revision + "/source.zip";
      const output = await schedulePreparations({
        masterKey: key,
        apiBase: "https://platform.test",
        app: {
          privateParticipantRepository: async (owner) => {
            assertEquals(owner, user);
            return privateRepo;
          },
          forkPublicSource: async (owner, url, pinned) => {
            assertEquals([owner, url, pinned], [
              user,
              source_location,
              source_kind === "repository" ? "f".repeat(40) : null,
            ]);
            forks++;
            return {
              repository: {
                ...privateRepo,
                full_name: organization + "/source-" + "c".repeat(20),
                private: false,
                fork: true,
              },
              sourceCommit: "d".repeat(40),
              sourceRepository: "example/project",
            };
          },
        },
        rpc: (name, args) => {
          if (name === "observer_placement") return Promise.resolve("AGENTIC-OBSERVER26-runner-9");
          if (name === "observer_runner_configuration") return Promise.resolve([{ organization }]);
          if (name === "observer_pending_preparations") {
            return Promise.resolve([{
              id: revision,
              owner_id: user,
              lease,
              source_kind,
              source_location,
              ...(source_kind === "repository" ? { submitted_commit: "f".repeat(40) } : {}),
              model_run_id: modelRun,
              model: "qwen-test",
              // The ZIP case also covers a v4 public-test scenario.
              gameplay: source_kind === "zip" ? "v4" : "v3",
            }]);
          }
          // Direct model access switched off: the model proxy as before.
          if (name === "observer_preparation_team_egress") return Promise.resolve({ enabled: false });
          // A revision submitted before snapshots existed: forked as before.
          if (name === "observer_preparation_source") return Promise.resolve(null);
          assertEquals(name, "observer_schedule_preparation");
          scheduled = args;
          return Promise.resolve(null);
        },
      });
      assertEquals(output, [{ id: revision, scheduled: true }]);
      assertEquals(scheduled.p_lease, lease);
      const j = scheduled.p_job;
      const input = JSON.parse(await decryptCredential(j.encrypted_input, j.id, key));
      assertEquals(await decryptCredential(j.encrypted_nonce, j.id + ":nonce", key), j.nonce);
      assertEquals(input.repository, { full_name: privateRepo.full_name });
      assertEquals(input.run_credential, "obs_" + modelRun + "." + scheduled.p_participant_token);
      // The team's own provider answers with the team's default model.
      assertEquals(input.model, "team-model");
      assertEquals(input.gameplay, source_kind === "zip" ? "v4" : undefined);
      assertEquals(input.scenario_ref, undefined);
      assertEquals(input.artifact_upload, { kind: "github" });
      assertEquals(input.archive_url, undefined);
      if (source_kind === "repository") {
        assertEquals(forks, 1);
        assertEquals(input.archive_ref, "github:" + organization + "/source-" + "c".repeat(20) + "@" + "d".repeat(40));
      } else {
        assertEquals(forks, 0);
        assertEquals(input.archive_storage_ref, { bucket: "observer-staging", path: source_location });
      }
    },
  );
}

Deno.test("an unavailable source does not open a model session or expose backend diagnostics", async () => {
  const { organization } = await placement(user, locate);
  const calls: string[] = [];
  const output = await schedulePreparations({
    masterKey: key,
    apiBase: "https://platform.test",
    app: {
      privateParticipantRepository: () => Promise.reject(new Error("credential-bearing response")),
      forkPublicSource: () => {
        throw new Error("must not fork");
      },
    },
    rpc: (name, args) => {
      if (name === "observer_placement") return Promise.resolve("AGENTIC-OBSERVER26-runner-9");
      calls.push(name);
      if (name === "observer_runner_configuration") return Promise.resolve([{ organization }]);
      if (name === "observer_pending_preparations") return Promise.resolve([{ id: revision, lease, owner_id: user }]);
      assertEquals(name, "observer_preparation_error");
      assertEquals(args.p_error, "preparation_unavailable");
      return Promise.resolve(null);
    },
  });
  assertEquals(output, [{ id: revision, scheduled: false, error: "preparation_unavailable" }]);
  assertEquals(calls.includes("observer_schedule_preparation"), false);
});

Deno.test("direct model access gives the adaptation the team's variables instead of the model proxy", async () => {
  const { organization, privateRepository } = await placement(user, locate);
  const privateRepo = {
    id: 42,
    full_name: organization + "/" + privateRepository,
    private: true,
    fork: false,
    default_branch: "main",
  };
  const variable = "00000000-0000-4000-8000-000000000009";
  let scheduled: Record<string, any> = {};
  const output = await schedulePreparations({
    masterKey: key,
    apiBase: "https://platform.test",
    app: {
      privateParticipantRepository: () => Promise.resolve(privateRepo),
      forkPublicSource: () => {
        throw new Error("must not fork");
      },
    },
    rpc: async (name, args) => {
      if (name === "observer_placement") return "AGENTIC-OBSERVER26-runner-9";
      if (name === "observer_runner_configuration") return [{ organization }];
      if (name === "observer_pending_preparations") {
        return [{
          id: revision,
          owner_id: user,
          lease,
          source_kind: "zip",
          source_location: user + "/" + revision + "/source.zip",
          model_run_id: modelRun,
        }];
      }
      if (name === "observer_preparation_team_egress") {
        assertEquals(args, { p_revision: revision });
        return {
          enabled: true,
          variables: [
            {
              id: variable,
              name: "OPENAI_API_KEY",
              secret: true,
              encrypted_value: await encryptCredential("sk-team", variable, key),
            },
            { id: "x", name: "OPENAI_MODEL", secret: false, plain_value: "team-model" },
          ],
          domains: ["api.example.com"],
        };
      }
      assertEquals(name, "observer_schedule_preparation");
      scheduled = args;
      return null;
    },
  });
  assertEquals(output, [{ id: revision, scheduled: true }]);
  const input = JSON.parse(await decryptCredential(scheduled.p_job.encrypted_input, scheduled.p_job.id, key));
  assertEquals(input.team_egress, {
    environment: { OPENAI_API_KEY: "sk-team", OPENAI_MODEL: "team-model" },
    secrets: ["OPENAI_API_KEY"],
    domains: ["api.example.com"],
  });
  assertEquals([input.model, input.model_base_url, input.run_credential], [undefined, undefined, undefined]);
});

Deno.test("a repository revision with a stored snapshot is prepared from exactly that snapshot, not a fork", async () => {
  const { organization, privateRepository } = await placement(user, locate);
  const snapshot = user + "/" + "f".repeat(40) + "-" + revision + ".zip";
  let scheduled: Record<string, any> = {};
  const output = await schedulePreparations({
    masterKey: key,
    apiBase: "https://platform.test",
    app: {
      privateParticipantRepository: () =>
        Promise.resolve({
          id: 42,
          full_name: organization + "/" + privateRepository,
          private: true,
          fork: false,
          default_branch: "main",
        }),
      forkPublicSource: () => Promise.reject(new Error("must not fork")),
    },
    rpc: (name, args) => {
      if (name === "observer_placement") return Promise.resolve(organization);
      if (name === "observer_runner_configuration") return Promise.resolve([{ organization }]);
      if (name === "observer_pending_preparations") {
        return Promise.resolve([{
          id: revision,
          owner_id: user,
          lease,
          source_kind: "repository",
          source_location: "https://github.com/example/project",
          submitted_commit: "f".repeat(40),
          model_run_id: modelRun,
          model: "qwen-test",
          gameplay: "v3",
        }]);
      }
      if (name === "observer_preparation_team_egress") return Promise.resolve({ enabled: false });
      if (name === "observer_preparation_source") {
        assertEquals(args, { p_revision: revision });
        return Promise.resolve(snapshot);
      }
      assertEquals(name, "observer_schedule_preparation");
      scheduled = args;
      return Promise.resolve(null);
    },
  });
  assertEquals(output, [{ id: revision, scheduled: true }]);
  const j = scheduled.p_job;
  const input = JSON.parse(await decryptCredential(j.encrypted_input, j.id, key));
  assertEquals(input.archive_storage_ref, { bucket: "observer-sources", path: snapshot });
  assertEquals(input.archive_ref, undefined);
});
