import { assertEquals, assertThrows } from "@std/assert";
import { archiveReference } from "./observer-app.ts";
import { GitHubError } from "./observer-github.ts";

const repo = "participant-" + "a".repeat(32), sha = "b".repeat(40);

Deno.test("artifact references accept all twelve runner organizations and nothing else", () => {
  for (let i = 1; i <= 12; i++) {
    const organization = "AGENTIC-OBSERVER26-runner-" + i;
    assertEquals(archiveReference("github:" + organization + "/" + repo + "@" + sha), {
      organization,
      repository: repo,
      commit: sha,
    });
  }
  assertEquals(
    archiveReference("github:AGENTIC-OBSERVER26-runner-10/source-" + "c".repeat(20) + "@" + sha, false).repository,
    "source-" + "c".repeat(20),
  );
  for (
    const value of [
      "github:AGENTIC-OBSERVER26-runner-100/" + repo + "@" + sha,
      "github:AGENTIC-OBSERVER26-runner-0/" + repo + "@" + sha,
      "github:outsider/" + repo + "@" + sha,
      "github:AGENTIC-OBSERVER26-runner-10/source-" + "c".repeat(20) + "@" + sha,
    ]
  ) assertThrows(() => archiveReference(value), GitHubError);
});
