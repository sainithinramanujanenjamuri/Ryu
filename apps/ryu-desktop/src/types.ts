/* TypeScript types for RYU Command Center */

export interface SpaceInfo {
  space_id: string;
  name: string;
  status: string;
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

