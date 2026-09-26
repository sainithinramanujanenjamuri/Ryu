/* Local Channel Daemon Client with WebCrypto token-hmac-v1 client-side signing */

import { ApprovalRequestData, AttentionState, PulseEvent, SpaceInfo, TaskItem } from "../types";

export const DAEMON_BASE_URL = "http://127.0.0.1:8420";

export class ApiClient {
  private baseUrl: string;
  private daemonToken: string;

  constructor(baseUrl: string = DAEMON_BASE_URL, daemonToken: string = "") {
    this.baseUrl = baseUrl;
    this.daemonToken = daemonToken || localStorage.getItem("ryu_daemon_token") || "";
  }

  setDaemonToken(token: string): void {
    this.daemonToken = token;
    localStorage.setItem("ryu_daemon_token", token);
  }

  getDaemonToken(): string {
    return this.daemonToken;
  }

  private async request<T>(method: string, path: string, body?: any): Promise<T> {
    const headers: Record<string, string> = {
      Accept: "application/json",
    };
    if (this.daemonToken) {
      headers["Authorization"] = `Bearer ${this.daemonToken}`;
    }
    if (body) {
      headers["Content-Type"] = "application/json";
    }

    const res = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    if (!res.ok) {
      let errMsg = res.statusText;
      try {
        const errJson = await res.json();
        if (errJson.error) errMsg = errJson.error;
      } catch {}
      throw new Error(`[${res.status}] ${errMsg}`);
    }

    return res.json();
  }

  async checkHealth(): Promise<{ status: string; phase: string }> {
    return this.request("GET", "/api/v1/health");
  }

  async listSpaces(): Promise<SpaceInfo[]> {
    const data = await this.request<{ spaces: SpaceInfo[] }>("GET", "/api/v1/spaces");
    return data.spaces;
  }

  async getAttention(spaceId: string): Promise<AttentionState> {
    return this.request<AttentionState>("GET", `/api/v1/spaces/${spaceId}/attention`);
  }

  async listApprovals(spaceId: string, status?: string): Promise<ApprovalRequestData[]> {
    const query = status ? `?status=${status}` : "";
    const data = await this.request<{ approvals: ApprovalRequestData[] }>(
      "GET",
      `/api/v1/spaces/${spaceId}/approvals${query}`
    );
    return data.approvals;
  }

  async getApproval(spaceId: string, approvalId: string): Promise<ApprovalRequestData> {
    return this.request<ApprovalRequestData>(
      "GET",
      `/api/v1/spaces/${spaceId}/approvals/${approvalId}`
    );
  }

  async listTasks(spaceId: string): Promise<TaskItem[]> {
    const data = await this.request<{ tasks: TaskItem[] }>("GET", `/api/v1/spaces/${spaceId}/tasks`);
    return data.tasks;
  }

  async getAudit(spaceId: string, limit: number = 50): Promise<PulseEvent[]> {
    const data = await this.request<{ events: PulseEvent[] }>(
      "GET",
      `/api/v1/spaces/${spaceId}/audit?limit=${limit}`
    );
    return data.events;
  }

  async getLLMConfig(): Promise<{
    enabled: boolean;
    provider: string;
    base_url: string;
    model: string;
    api_key_masked?: string;
  }> {
    return this.request("GET", "/api/v1/config/llm");
  }

  async setLLMConfig(config: {
    enabled?: boolean;
    provider?: string;
    base_url?: string;
    model?: string;
    api_key?: string;
  }): Promise<{ success: boolean; config: any }> {
    return this.request("POST", "/api/v1/config/llm", config);
  }

  async sendPrompt(spaceId: string, prompt: string, liveLLM?: boolean): Promise<{
    command_id: string;
    space_id: string;
    goal_id: string;
    objective: string;
    required_capabilities: string[];
    single_agent_eligible: boolean;
    execution_mode: string;
    response: string;
    status: string;
  }> {
    return this.request("POST", `/api/v1/spaces/${spaceId}/prompt`, {
      prompt,
      live_llm: liveLLM,
    });
  }

  /**
   * Client-side cryptographic HMAC-SHA256 signing via WebCrypto.
   * The secretKey is never transmitted to the daemon.
   */
  async signAndSubmitDecision(
    approverId: string,
    secretKey: string,
    spaceId: string,
    approvalId: string,
    decision: "APPROVE" | "REJECT",
    planVersion: number,
    capabilityRequestHash: string
  ): Promise<{ success: boolean; approval_id: string }> {
    const timestamp = Math.floor(Date.now() / 1000);
    const nonce = Array.from(crypto.getRandomValues(new Uint8Array(16)))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");

    // Canonical 9-field pre-image
    const preimage = [
      "token-hmac-v1",
      approverId,
      timestamp.toString(),
      nonce,
      spaceId,
      approvalId,
      decision.toUpperCase(),
      planVersion.toString(),
      capabilityRequestHash.toLowerCase(),
    ].join("\n");

    const encoder = new TextEncoder();
    const keyData = encoder.encode(secretKey);
    const cryptoKey = await crypto.subtle.importKey(
      "raw",
      keyData,
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"]
    );

    const signatureBuffer = await crypto.subtle.sign(
      "HMAC",
      cryptoKey,
      encoder.encode(preimage)
    );

    const signature = Array.from(new Uint8Array(signatureBuffer))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");

    // Submit only the cryptographic signature, never the secretKey
    return this.request(
      "POST",
      `/api/v1/spaces/${spaceId}/approvals/${approvalId}/decision`,
      {
        approver_id: approverId,
        timestamp,
        nonce,
        decision: decision.toUpperCase(),
        plan_version: planVersion,
        capability_request_hash: capabilityRequestHash,
        signature,
      }
    );
  }

  subscribeEvents(
    spaceId: string,
    onPulse: (pulse: PulseEvent) => void,
    onError?: (err: any) => void
  ): () => void {
    const token = this.daemonToken ? `?token=${encodeURIComponent(this.daemonToken)}` : "";
    const es = new EventSource(`${this.baseUrl}/api/v1/spaces/${spaceId}/events${token}`);

    es.onmessage = (event) => {
      try {
        const pulse = JSON.parse(event.data);
        if (pulse && pulse.id) {
          onPulse(pulse);
        }
      } catch {}
    };

    if (onError) {
      es.onerror = onError;
    }

    return () => es.close();
  }
}

export const api = new ApiClient();

