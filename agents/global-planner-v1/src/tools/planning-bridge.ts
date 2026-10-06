import { Agent, raw_lines } from "../agent";
import { planningFrame } from "../planning";

const send = (value: unknown) => process.stdout.write(JSON.stringify(value) + "\n");
async function main() {
  const lines = raw_lines(process.stdin)[Symbol.asyncIterator]();
  let agent: Agent | undefined;
  try {
    while (true) {
      const line = await lines.next();
      if (line.done) break;
      const msg = JSON.parse(line.value);
      if (msg.operation === "close") break;
      if (msg.operation === "initialize") {
        await agent?.close();
        agent = new Agent(msg.payload);
        if (!msg.rule_only) agent.choosePlan = async (plans, actions, teacher, snapshot, t, night) => {
          const frame = await planningFrame(agent!, plans, actions, teacher, snapshot, t, night);
          send({type: "proposal", frame});
          const line = await lines.next();
          if (line.done) throw new Error("No planning choice");
          const choice = JSON.parse(line.value);
          if (choice.operation !== "choose") throw new Error("Expected planning choice");
          return choice.index;
        };
        send({type: "ready"});
      } else if (msg.operation === "decide") {
        if (!agent) throw new Error("Initialize required");
        const action = await agent.decide(msg.payload);
        agent.decisions++;
        send({type: "action", action, cpu: process.cpuUsage(), rss: process.memoryUsage().rss});
      } else throw new Error("Unknown bridge operation");
    }
  } finally { await agent?.close(); }
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
