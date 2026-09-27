/* TypeScript types for RYU Command Center */

export interface SpaceInfo {
  space_id: string;
  name: string;
  status: string;
  owner_id?: string;
  created_at?: number;
  budget?: number;
  attention_limit?: number;
}

export interface AttentionState {
  space_id: string;
  concurrency_limit: number;
  active_count: number;
  queued_count: number;
  is_saturated: boolean;
  active_approvals: ApprovalRequestData[];
  queued_approvals: ApprovalRequestData[];
}

export interface ApprovalRequestData {
  approval_id: string;
  request_id?: string;
  space_id: string;
  status: "pending" | "approved" | "denied" | "expired" | "held" | "consumed";
  queue_state: "active" | "queued" | "resolved";
  risk_tier: "low" | "standard" | "high" | "critical";
  capability: string;
  capability_name?: string;
  requester_id: string;
  approver_id: string;
  plan_version: number;
  capability_request_hash: string;
  parameters?: Record<string, any>;
  taint: boolean;
  summary: string;
  timeout_class: "default_deny" | "default_hold";
  created_at: number;
  expires_at: number;
}

export interface PulseEvent {
  id: string;
  type: string;
  severity: "debug" | "info" | "warning" | "error" | "critical";
  space_id: string;
  timestamp: string;
  payload: Record<string, any>;
  taint: boolean;
}

export interface TaskItem {
  task_id: string;
  title: string;
  status: string;
}

export interface DialogueTurn {
  turn_id: string;
  space_id: string;
  user_prompt: string;
  assistant_response: string;
  timestamp: number;
  goal_id?: string | null;
  status: string;
  single_agent_eligible?: boolean;
  required_capabilities?: string[];
  artifacts?: string[];
}

export interface ArtifactItem {
  artifact_id: string;
  space_id: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  created_at: number;
  metadata?: Record<string, any>;
}

export interface NodeDevice {
  device_id: string;
  device_type: string;
  state: string;
}

export interface NodeInfoItem {
  node_id: string;
  platform: string;
  runtime_state: string;
  trust_tier: string;
  device_count: number;
  devices: NodeDevice[];
}

export interface MemoryExperience {
  experience_id: string;
  outcome: string;
  counterfactual: string;
  situation?: Record<string, any>;
}

export interface GlobalKnowledgeItem {
  knowledge_id: string;
  topic: string;
  content: string;
}

export interface MemoryStateItem {
  space_id: string;
  experiences: MemoryExperience[];
  experience_count: number;
  global_knowledge: GlobalKnowledgeItem[];
}
