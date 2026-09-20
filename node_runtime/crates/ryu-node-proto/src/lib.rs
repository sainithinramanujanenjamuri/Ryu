//! ryu-node-proto: Node Runtime Protocol Contract Types
//! Space-Centric Cognitive Architecture (SCCA) — Phase 7

pub mod generated;

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum RiskTier {
    Low,
    High,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NodeState {
    Registered,
    Ready,
    Active,
    Draining,
    Offline,
    Terminated,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceType {
    Cpu,
    Gpu,
    Storage,
    Network,
    Screen,
    Terminal,
    Filesystem,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceState {
    Discovered,
    Online,
    Busy,
    Offline,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NodeInfo {
    pub node_id: String,
    pub platform: String,
    pub architecture: String,
    pub environment_profile: String,
    pub runtime_state: NodeState,
    pub cpu_cores: u32,
    pub memory_total_bytes: u64,
    pub storage_total_bytes: u64,
    pub capabilities: Vec<String>,
    pub labels: HashMap<String, String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DeviceInfo {
    pub device_id: String,
    pub node_id: String,
    pub device_type: DeviceType,
    pub capability_metadata: HashMap<String, String>,
    pub availability_state: DeviceState,
    pub total_capacity: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NodeCapabilityGrant {
    pub grant_id: String,
    pub space_id: String,
    pub worker_id: String,
    pub node_id: String,
    pub device_id: String,
    pub capability: String,
    pub lease_token: String,
    pub nonce: String,
    pub issued_at: String,
    pub expiry: String,
    pub risk_tier: RiskTier,
    pub grant_schema_version: String,
    pub revocation_token: String,
    pub signature: String,
}

impl NodeCapabilityGrant {
    /// Canonical payload format for cryptographic HMAC-SHA256 signature verification.
    pub fn canonical_payload(&self) -> String {
        format!(
            "{}|{}|{}|{}|{}|{}|{}|{}|{}|{}|{}|{}",
            self.grant_schema_version,
            self.grant_id,
            self.space_id,
            self.worker_id,
            self.node_id,
            self.device_id,
            self.capability,
            self.lease_token,
            self.nonce,
            self.issued_at,
            self.expiry,
            match self.risk_tier {
                RiskTier::Low => "low",
                RiskTier::High => "high",
            }
        )
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DeviceBindingRequest {
    pub grant: NodeCapabilityGrant,
    pub device_id: String,
    pub binding_id: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DeviceBindingResponse {
    pub bound: bool,
    pub binding_id: String,
    pub error: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HealthReport {
    pub status: String,
    pub cpu_percent: f64,
    pub memory_used_bytes: u64,
    pub memory_total_bytes: u64,
    pub uptime_seconds: u64,
    pub active_bindings_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuditEntry {
    pub seq: u64,
    pub timestamp: String,
    pub node_id: String,
    pub space_id: String,
    pub event_type: String,
    pub grant_id: String,
    pub device_id: String,
    pub operation_id: String,
    pub result: String,
    pub prev_hash: String,
    pub record_hash: String,
}
