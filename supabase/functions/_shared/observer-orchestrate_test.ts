import { assert, assertEquals, assertNotEquals, assertThrows } from "@std/assert";
import { decryptCredential, encryptCredential } from "./observer-model.ts";
import { placement } from "./observer-github.ts";

// Recorded placement from public.observer_placement; runner-9 is one of the added organizations.
const locate = () => Promise.resolve("AGENTIC-OBSERVER26-runner-9");
import { scheduleRuns, scheduleScores } from "./observer-orchestrate.ts";
import { validateJobPayload } from "./observer-job.ts";

const key = btoa("k".repeat(32));
const user = "00000000-0000-4000-8000-000000000001";
const run = "00000000-0000-4000-8000-000000000002";
const lease = "00000000-0000-4000-8000-000000000003";

for (const randomized of [false, true]) {
  for (const mode of ["local", "project"]) {
    Deno.test(
      mode + (randomized ? " randomized" : " fixed") +
        " scheduler encrypts separate capabilities and publishes no secrets in receipts",
      async () => {
        const { organization } = await placement(user, locate);
        const manifest = {
          schema_version: "observer-project-v1",
          image: "python@sha256:" + "a".repeat(64),
          run: ["python3", "agent.py"],
        };
        const archive = "github:" + organization + "/participant-" + user.replaceAll("-", "") + "@" + "b".repeat(40);
        const instance = randomized
          ? {
            seed: "f".repeat(64),
            profile_id: lease,
            bundle_digest: "c".repeat(64),
            max_candidates: 32,
            profile: { schema_version: "observer-calibration-profile-v1" },
          }
          : null;
        let scheduled: Record<string, any> = {}, provisioned = false;
        const output = await scheduleRuns({
          masterKey: key,
          apiBase: "https://platform.test",
          ensureRepository: async (owner) => {
            assertEquals(owner, user);
            provisioned = true;
          },
          rpc: (name, args) => {
            if (name === "observer_placement") return Promise.resolve("AGENTIC-OBSERVER26-runner-9");
            if (name === "observer_runner_configuration") return Promise.resolve([{ organization }]);
            if (name === "observer_instance_input") return Promise.resolve(instance);
            if (name === "observer_run_colocated") return Promise.resolve(false);
            if (name === "observer_hardening") return Promise.resolve({ restricted_egress: true, rescore: true });
            if (name === "observer_run_team_egress") return Promise.resolve({ enabled: false });
            if (name === "observer_pending_runs") {
              return Promise.resolve([{
                id: run,
                user_id: user,
                lease,
                mode,
                storage_path: "scenario/bundle.zip",
                scenario_digest: "c".repeat(64),
                runtime_seconds: 3600,
                archive_ref: archive,
                materialized_digest: "d".repeat(64),
                manifest,
              }]);
            }
            assertEquals(name, "observer_schedule_run");
            assertEquals(provisioned, true);
            scheduled = args;
            return Promise.resolve(null);
          },
        });
        assertEquals(output, [{ id: run, scheduled: true }]);
        assertEquals(JSON.stringify(output).includes("f".repeat(64)), false);
        assertEquals(scheduled.p_organization, organization);
        assertEquals(scheduled.p_lease, lease);
        assertNotEquals(scheduled.p_participant_token, scheduled.p_engine_token);
        assertEquals(scheduled.p_jobs.length, mode === "local" ? 1 : 2);
        for (const job of scheduled.p_jobs) {
          assertEquals(await decryptCredential(job.encrypted_nonce, job.id + ":nonce", key), job.nonce);
          const input = JSON.parse(await decryptCredential(job.encrypted_input, job.id, key));
          assertEquals(input.job_id, job.id);
          assertEquals(input.run_id, run);
          if (job.kind === "engine") {
            assertEquals(input.instance, instance ?? undefined);
            assertEquals(input.run_credential, "obs_" + run + "." + scheduled.p_engine_token);
            assertEquals(input.scenario_ref, { bucket: "observer-scenarios", path: "scenario/bundle.zip" });
            assertEquals(input.archive_ref, undefined);
            delete input.scenario_ref;
            input.scenario_url = "https://storage.test/fresh.zip";
          } else {
            assertEquals(input.instance, undefined);
            assertEquals(input.run_credential, "obs_" + run + "." + scheduled.p_participant_token);
            assertEquals(input.archive_ref, archive);
            assertEquals(input.scenario_ref, undefined);
            assertEquals(input.scenario_digest, undefined);
            delete input.archive_ref;
            input.archive_url = "https://codeload.github.com/fresh.zip";
          }
          validateJobPayload(input, {
            organization,
            organizationId: "101",
            repositoryId: "303",
            approvedSha: "e".repeat(40),
            workflow: job.kind === "engine" ? "observer-engine.yml" : "observer-execute.yml",
          }, job.id);
          if (job.kind === "execute" && instance) {
            assertThrows(() =>
              validateJobPayload({ ...input, instance }, {
                organization,
                organizationId: "101",
                repositoryId: "303",
                approvedSha: "e".repeat(40),
                workflow: "observer-execute.yml",
              }, job.id)
            );
          }
        }
        if (mode === "local") {
          assertEquals(
            await decryptCredential(scheduled.p_local_credential, run + ":local", key),
            "obs_" + run + "." + scheduled.p_participant_token,
          );
        } else assertEquals(scheduled.p_local_credential, null);
      },
    );
  }
}

Deno.test("unconfigured deployments do not consume retries; provisioning failures never open a session", async () => {
  const { organization } = await placement(user, locate);
  const calls: string[] = [];
  const disabled = await scheduleRuns({
    masterKey: key,
    apiBase: "https://platform.test",
    ensureRepository: () => {
      throw new Error("must not provision");
    },
    rpc: (name) => {
      if (name === "observer_placement") return Promise.resolve("AGENTIC-OBSERVER26-runner-9");
      calls.push(name);
      return Promise.resolve([]);
    },
  });
  assertEquals(disabled, []);
  assertEquals(calls, ["observer_runner_configuration"]);
  calls.length = 0;
  const output = await scheduleRuns({
    masterKey: key,
    apiBase: "https://platform.test",
    ensureRepository: () => {
      throw new Error("sensitive installation error");
    },
    rpc: (name, args) => {
      if (name === "observer_placement") return Promise.resolve("AGENTIC-OBSERVER26-runner-9");
      calls.push(name);
      if (name === "observer_runner_configuration") return Promise.resolve([{ organization }]);
      if (name === "observer_pending_runs") return Promise.resolve([{ id: run, user_id: user, lease, mode: "local" }]);
      assertEquals(name, "observer_run_schedule_error");
      assertEquals(args, { p_run: run, p_lease: lease, p_error: "schedule_unavailable" });
      return Promise.resolve(null);
    },
  });
  assertEquals(output, [{ id: run, scheduled: false, error: "schedule_unavailable" }]);
  assertEquals(calls.includes("observer_schedule_run"), false);
});

Deno.test("a final formal run whose instance lookup is refused is never scheduled", async () => {
  const { organization } = await placement(user, locate);
  const calls: string[] = [];
  const output = await scheduleRuns({
    masterKey: key,
    apiBase: "https://platform.test",
    ensureRepository: async () => {},
    rpc: (name, args) => {
      if (name === "observer_placement") return Promise.resolve("AGENTIC-OBSERVER26-runner-9");
      calls.push(name);
      if (name === "observer_runner_configuration") return Promise.resolve([{ organization }]);
      if (name === "observer_run_team_egress") return Promise.resolve({ enabled: false });
      if (name === "observer_pending_runs") {
        return Promise.resolve([{ id: run, user_id: user, lease, mode: "project" }]);
      }
      // observer_instance_input raises formal_instance_missing instead of returning NULL.
      if (name === "observer_instance_input") return Promise.reject(new Error("formal_instance_missing"));
      assertEquals(name, "observer_run_schedule_error");
      assertEquals(args, { p_run: run, p_lease: lease, p_error: "schedule_unavailable" });
      return Promise.resolve(null);
    },
  });
  assertEquals(output, [{ id: run, scheduled: false, error: "schedule_unavailable" }]);
  assertEquals(calls.includes("observer_schedule_run"), false);
});

Deno.test("a colocated public run gets one engine job that also starts the participant", async () => {
  const { organization } = await placement(user, locate);
  const manifest = {
    schema_version: "observer-project-v1",
    image: "python@sha256:" + "a".repeat(64),
    run: ["python3", "agent.py"],
  };
  const archive = "github:" + organization + "/participant-" + user.replaceAll("-", "") + "@" + "b".repeat(40);
  let scheduled: Record<string, any> = {};
  const output = await scheduleRuns({
    masterKey: key,
    apiBase: "https://platform.test",
    ensureRepository: async () => {},
    rpc: (name, args) => {
      if (name === "observer_placement") return Promise.resolve("AGENTIC-OBSERVER26-runner-9");
      if (name === "observer_runner_configuration") return Promise.resolve([{ organization }]);
      if (name === "observer_instance_input") return Promise.resolve(null);
      if (name === "observer_run_colocated") return Promise.resolve(true);
      if (name === "observer_run_team_egress") return Promise.resolve({ enabled: false });
      if (name === "observer_pending_runs") {
        return Promise.resolve([{
          id: run,
          user_id: user,
          lease,
          mode: "project",
          storage_path: "scenario/bundle.zip",
          scenario_digest: "c".repeat(64),
          runtime_seconds: 3600,
          archive_ref: archive,
          materialized_digest: "d".repeat(64),
          manifest,
        }]);
      }
      scheduled = args;
      return Promise.resolve(null);
    },
  });
  assertEquals(output, [{ id: run, scheduled: true }]);
  assertEquals(scheduled.p_jobs.map((job: { kind: string }) => job.kind), ["engine"]);
  const job = scheduled.p_jobs[0];
  const input = JSON.parse(await decryptCredential(job.encrypted_input, job.id, key));
  assertEquals(input.archive_ref, archive);
  assertEquals(input.colocated.run_credential, "obs_" + run + "." + scheduled.p_participant_token);
  assertEquals(input.run_credential, "obs_" + run + "." + scheduled.p_engine_token);
  assertEquals(input.colocated.source_digest, "d".repeat(64));
});

for (const restricted of [true, false]) {
  Deno.test(
    "a colocated engine job carries the restricted egress switch only while it is " + (restricted ? "on" : "off"),
    async () => {
      const organization = "AGENTIC-OBSERVER26-runner-9";
      const manifest = {
        schema_version: "observer-project-v1",
        image: "python@sha256:" + "a".repeat(64),
        run: ["python3", "agent.py"],
      };
      const archive = "github:" + organization + "/participant-" + user.replaceAll("-", "") + "@" + "b".repeat(40);
      let scheduled: Record<string, any> = {}, switchReads = 0;
      const output = await scheduleRuns({
        masterKey: key,
        apiBase: "https://platform.test",
        ensureRepository: () => Promise.resolve(),
        rpc: (name, args) => {
          if (name === "observer_placement") return Promise.resolve(organization);
          if (name === "observer_runner_configuration") return Promise.resolve([{ organization }]);
          if (name === "observer_instance_input") return Promise.resolve(null);
          if (name === "observer_run_colocated") return Promise.resolve(true);
          if (name === "observer_hardening") {
            switchReads++;
            return Promise.resolve({ restricted_egress: restricted, rescore: true });
          }
          if (name === "observer_run_team_egress") return Promise.resolve({ enabled: false });
          if (name === "observer_pending_runs") {
            return Promise.resolve([run, lease].map((id) => ({
              id,
              user_id: user,
              lease,
              mode: "project",
              storage_path: "scenario/bundle.zip",
              scenario_digest: "c".repeat(64),
              runtime_seconds: 900,
              archive_ref: archive,
              materialized_digest: "d".repeat(64),
              manifest,
            })));
          }
          assertEquals(name, "observer_schedule_run");
          if (args.p_run === run) scheduled = args;
          return Promise.resolve(null);
        },
      });
      assertEquals(output.map((o) => o.scheduled), [true, true]);
      assertEquals(switchReads, 1);
      assertEquals(scheduled.p_jobs.length, 1);
      const [job] = scheduled.p_jobs;
      const input = JSON.parse(await decryptCredential(job.encrypted_input, job.id, key));
      assertEquals(input.restricted_egress, restricted ? true : undefined);
      assertEquals(input.colocated.model_base_url, "https://platform.test/functions/v1/observer-model/v1");
      delete input.scenario_ref;
      delete input.archive_ref;
      input.scenario_url = "https://storage.test/fresh.zip";
      input.archive_url = "https://codeload.github.com/fresh.zip";
      const expected = {
        organization,
        organizationId: "101",
        repositoryId: "303",
        approvedSha: "e".repeat(40),
        workflow: "observer-engine.yml" as const,
      };
      validateJobPayload(input, expected, job.id);
      // The flag is meaningful only next to a colocated container, and only as true.
      assertThrows(() => validateJobPayload({ ...input, restricted_egress: false }, expected, job.id));
      const { colocated: _c, archive_url: _a, ...split } = input;
      assertThrows(() => validateJobPayload({ ...split, restricted_egress: true }, expected, job.id));
    },
  );
}

for (const [colocated, modelDisabled] of [[true, false], [false, false], [true, true], [false, true]]) {
  Deno.test(
    "team egress puts the team's variables and domains into the " + (colocated ? "engine" : "execute") + " job" +
      (modelDisabled ? " (evaluation without a model)" : ""),
    async () => {
      const organization = "AGENTIC-OBSERVER26-runner-9";
      const manifest = {
        schema_version: "observer-project-v1",
        image: "python@sha256:" + "a".repeat(64),
        run: ["python3", "agent.py"],
      };
      const archive = "github:" + organization + "/participant-" + user.replaceAll("-", "") + "@" + "b".repeat(40);
      const variable = crypto.randomUUID();
      const cipher = await encryptCredential("sk-team-key-0123456789", variable, key);
      let scheduled: Record<string, any> = {};
      await scheduleRuns({
        masterKey: key,
        apiBase: "https://platform.test",
        ensureRepository: () => Promise.resolve(),
        rpc: (name, args) => {
          if (name === "observer_placement") return Promise.resolve(organization);
          if (name === "observer_runner_configuration") return Promise.resolve([{ organization }]);
          if (name === "observer_instance_input") return Promise.resolve(null);
          if (name === "observer_run_colocated") return Promise.resolve(colocated);
          if (name === "observer_hardening") {
            return Promise.resolve({ restricted_egress: true, rescore: true, team_egress: true });
          }
          if (name === "observer_run_team_egress") {
            assertEquals(args.p_run, run);
            return Promise.resolve({
              enabled: true,
              ...(modelDisabled ? { model_disabled: true } : {}),
              variables: [
                { id: variable, name: "OPENAI_API_KEY", secret: true, encrypted_value: cipher, plain_value: null },
                {
                  id: crypto.randomUUID(),
                  name: "OPENAI_MODEL",
                  secret: false,
                  encrypted_value: null,
                  plain_value: "k3",
                },
              ],
              domains: ["api.kimi.com"],
            });
          }
          if (name === "observer_run_team_egress") return Promise.resolve({ enabled: false });
          if (name === "observer_pending_runs") {
            return Promise.resolve([{
              id: run,
              user_id: user,
              lease,
              mode: "project",
              storage_path: "scenario/bundle.zip",
              scenario_digest: "c".repeat(64),
              runtime_seconds: 900,
              archive_ref: archive,
              materialized_digest: "d".repeat(64),
              manifest,
            }]);
          }
          assertEquals(name, "observer_schedule_run");
          scheduled = args;
          return Promise.resolve(null);
        },
      });
      const job = scheduled.p_jobs.find((j: { kind: string }) => j.kind === (colocated ? "engine" : "execute"));
      // The plaintext key exists only inside the encrypted job input.
      assert(!JSON.stringify(scheduled).includes("sk-team-key"));
      const input = JSON.parse(await decryptCredential(job.encrypted_input, job.id, key));
      assertEquals(input.team_egress, {
        environment: { OPENAI_API_KEY: "sk-team-key-0123456789", OPENAI_MODEL: "k3" },
        secrets: ["OPENAI_API_KEY"],
        domains: ["api.kimi.com"],
      });
      // Without a model: the flag goes where the participant container runs (the database has
      // already left out the model variables), never anywhere else.
      assertEquals(input.model_disabled, modelDisabled ? true : undefined);
      if (!colocated) {
        const engine = scheduled.p_jobs.find((j: { kind: string }) => j.kind === "engine");
        const engineInput = JSON.parse(await decryptCredential(engine.encrypted_input, engine.id, key));
        assertEquals(engineInput.team_egress, undefined);
        assertEquals(engineInput.model_disabled, undefined);
      }
      delete input.scenario_ref;
      delete input.archive_ref;
      input.archive_url = "https://codeload.github.com/fresh.zip";
      if (colocated) input.scenario_url = "https://storage.test/fresh.zip";
      const expected = {
        organization,
        organizationId: "101",
        repositoryId: "303",
        approvedSha: "e".repeat(40),
        workflow: (colocated ? "observer-engine.yml" : "observer-execute.yml") as
          | "observer-engine.yml"
          | "observer-execute.yml",
      };
      validateJobPayload(input, expected, job.id);
      for (
        const bad of [
          { ...input.team_egress, domains: ["127.0.0.1"] },
          { ...input.team_egress, domains: ["svc.internal"] },
          { ...input.team_egress, environment: { OBSERVER_RUN_TOKEN: "x" }, secrets: [] },
          { ...input.team_egress, environment: { HTTPS_PROXY: "http://evil" }, secrets: [] },
          { ...input.team_egress, secrets: ["MISSING"] },
          { ...input.team_egress, extra: 1 },
        ]
      ) assertThrows(() => validateJobPayload({ ...input, team_egress: bad }, expected, job.id));
      validateJobPayload({ ...input, model_disabled: true }, expected, job.id);
      for (const bad of [false, 1, "1"]) {
        assertThrows(() => validateJobPayload({ ...input, model_disabled: bad }, expected, job.id));
      }
    },
  );
}

Deno.test("score jobs carry only the committed trace references and go to the engine's organization", async () => {
  const organization = "AGENTIC-OBSERVER26-runner-4";
  const result = "github:" + organization + "/participant-" + user.replaceAll("-", "") + "@" + "9".repeat(40);
  const enqueued: Record<string, any>[] = [];
  const output = await scheduleScores({
    masterKey: key,
    rpc: (name, args) => {
      if (name === "observer_runner_configuration") return Promise.resolve([{ organization }]);
      if (name === "observer_pending_score_runs") {
        return Promise.resolve([{
          id: run,
          organization,
          storage_path: "cards/v4-a.zip",
          scenario_digest: "c".repeat(64),
          result_path: result,
          decisions_digest: "d".repeat(64),
          termination_reason: "agent_finished",
        }]);
      }
      assertEquals(name, "observer_enqueue_job");
      enqueued.push(args);
      return Promise.resolve(args.p_id);
    },
  });
  assertEquals(output, [{ id: run, scheduled: true }]);
  const [job] = enqueued;
  assertEquals([job.p_kind, job.p_run, job.p_revision, job.p_organization], ["score", run, null, organization]);
  assertEquals(await decryptCredential(job.p_encrypted_nonce, job.p_id + ":nonce", key), job.p_nonce);
  const input = JSON.parse(await decryptCredential(job.p_encrypted_input, job.p_id, key));
  assertEquals(input, {
    run_id: run,
    scenario_ref: { bucket: "observer-scenarios", path: "cards/v4-a.zip" },
    scenario_digest: "c".repeat(64),
    result_ref: result,
    decisions_digest: "d".repeat(64),
    termination_reason: "agent_finished",
    kind: "score",
    job_id: job.p_id,
  });
  // As claimed: references resolved to short-lived downloads, nothing else.
  const { scenario_ref: _s, result_ref: _r, ...claimed } = input;
  const expected = {
    organization,
    organizationId: "101",
    repositoryId: "303",
    approvedSha: "e".repeat(40),
    workflow: "observer-score.yml" as const,
  };
  const payload = {
    ...claimed,
    scenario_url: "https://storage.test/card.zip",
    result_url: "https://codeload.github.com/result.zip",
  };
  validateJobPayload(payload, expected, job.p_id);
  for (
    const forged of [
      { ...payload, run_credential: "obs_" + run + "." + "x".repeat(43) },
      { ...payload, session_url: "https://platform.test/functions/v1/observer-session" },
      { ...payload, decisions_digest: "not-a-digest" },
      { ...payload, result_url: "http://codeload.github.com/result.zip" },
    ]
  ) assertThrows(() => validateJobPayload(forged, expected, job.p_id));
});

Deno.test("no runner configuration schedules no score job", async () => {
  const output = await scheduleScores({
    masterKey: key,
    rpc: (name) => {
      assertEquals(name, "observer_runner_configuration");
      return Promise.resolve([]);
    },
  });
  assertEquals(output, []);
});

Deno.test("open egress sends no domain list and the job API accepts only open:true with no domains", async () => {
  const { decodeTeamEgress } = await import("./observer-orchestrate.ts");
  const variable = crypto.randomUUID();
  const cipher = await encryptCredential("sk-team-key-0123456789", variable, key);
  const value = {
    enabled: true,
    open: true,
    variables: [{ id: variable, name: "OPENAI_API_KEY", secret: true, encrypted_value: cipher, plain_value: null }],
    domains: ["api.kimi.com"],
  };
  const team = await decodeTeamEgress(value, key);
  assertEquals(team, {
    environment: { OPENAI_API_KEY: "sk-team-key-0123456789" },
    secrets: ["OPENAI_API_KEY"],
    domains: [],
    open: true,
  });
  assertEquals((await decodeTeamEgress({ ...value, open: false }, key))?.domains, ["api.kimi.com"]);
  assertEquals(await decodeTeamEgress({ ...value, enabled: false }, key), null);
  // The egress route travels as a label only (nodes are added at claim time), with open egress only.
  const route = { name: "overseas", fallback: true, cap_bytes: 524288000 };
  const routeOf = (team: unknown) => (team as { route?: unknown } | null)?.route;
  assertEquals(routeOf(await decodeTeamEgress({ ...value, route }, key)), route);
  assertEquals(routeOf(await decodeTeamEgress({ ...value, route: null }, key)), undefined);
  assertEquals(routeOf(await decodeTeamEgress({ ...value, open: false, route }, key)), undefined);
  assertEquals(routeOf(await decodeTeamEgress({ ...value, route: { ...route, name: "direct" } }, key)), undefined);
  const run = crypto.randomUUID();
  const base = {
    kind: "execute",
    job_id: "11111111-1111-4111-8111-111111111111",
    run_id: run,
    archive_url: "https://codeload.github.com/x.zip",
    source_digest: "d".repeat(64),
    manifest: { schema_version: "observer-project-v1", image: "python@sha256:" + "a".repeat(64), run: ["python3"] },
    session_url: "https://platform.test/functions/v1/observer-session",
    run_credential: "obs_" + run + "." + "p".repeat(43),
    model_base_url: "https://platform.test/functions/v1/observer-model/v1",
  };
  const expected = {
    organization: "AGENTIC-OBSERVER26-runner-9",
    organizationId: "101",
    repositoryId: "303",
    approvedSha: "e".repeat(40),
    workflow: "observer-execute.yml" as const,
  };
  validateJobPayload({ ...base, team_egress: team }, expected, base.job_id);
  for (const bad of [{ ...team, open: false }, { ...team, domains: ["api.kimi.com"] }, { ...team, open: "yes" }]) {
    assertThrows(() => validateJobPayload({ ...base, team_egress: bad }, expected, base.job_id));
  }
});
