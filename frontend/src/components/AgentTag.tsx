import { agentColor } from "../palette";

/** Agent name with its fixed colour swatch (identity never relies on colour alone). */
export function AgentTag({ agent }: { agent: string }) {
  return (
    <span className="agent-tag">
      <span className="swatch" style={{ background: agentColor(agent) }} aria-hidden="true" />{agent}</span>
  );
}
