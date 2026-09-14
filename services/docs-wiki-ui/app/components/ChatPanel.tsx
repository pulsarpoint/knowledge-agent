import SharedChatPanel, { type ChatPanelProps } from "@knowledge-agent/chat-ui";
import * as client from "../lib/api";
import { addRecent } from "../lib/recentChats";

export default function ChatPanel(props: Omit<ChatPanelProps, "client" | "onRecent">) {
  return <SharedChatPanel {...props} client={client} onRecent={addRecent} />;
}
