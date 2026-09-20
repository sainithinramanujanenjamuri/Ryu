//! ryu-node: Physical Node Runtime Implementation
//! Space-Centric Cognitive Architecture (SCCA) — Phase 7

pub mod audit;
pub mod grant;
pub mod platform;

use audit::DeviceAuditLogger;
use grant::GrantVerifier;
use platform::PlatformAdapter;
use ryu_node_proto::{
    DeviceBindingRequest, DeviceBindingResponse, DeviceInfo, HealthReport, NodeCapabilityGrant, NodeInfo,
};
use std::collections::HashMap;

pub fn version() -> &'static str {
    "0.1.0"
}

pub struct NodeRuntime {
    pub node_id: String,
    pub shared_secret: String,
    pub audit_logger: DeviceAuditLogger,
    /// Maps binding_id -> grant_id
    pub active_bindings: HashMap<String, String>,
    /// Set of revoked revocation tokens
    pub revocations: Vec<String>,
}

impl NodeRuntime {
    pub fn new(node_id: &str, shared_secret: &str, audit_log_path: &str) -> Result<Self, String> {
        let audit_logger = DeviceAuditLogger::open(audit_log_path)?;
        Ok(Self {
            node_id: node_id.to_string(),
            shared_secret: shared_secret.to_string(),
            audit_logger,
            active_bindings: HashMap::new(),
            revocations: Vec::new(),
        })
    }

    pub fn inspect(&self) -> NodeInfo {
        PlatformAdapter::inspect_node(&self.node_id)
    }

    pub fn inspect_devices(&self) -> Vec<DeviceInfo> {
        PlatformAdapter::discover_devices(&self.node_id)
    }

    pub fn validate_grant(&self, grant: &NodeCapabilityGrant, current_time: &str) -> Result<(), String> {
        GrantVerifier::validate_grant(grant, &self.node_id, &self.shared_secret, &self.revocations, current_time)
    }

    pub fn bind_device(&mut self, req: DeviceBindingRequest, current_time: &str) -> DeviceBindingResponse {
        // Check if binding already exists (idempotency repeat check)
        if let Some(existing_grant_id) = self.active_bindings.get(&req.binding_id) {
            if existing_grant_id == &req.grant.grant_id {
                return DeviceBindingResponse {
                    bound: true,
                    binding_id: req.binding_id.clone(),
                    error: None,
                };
            } else {
                return DeviceBindingResponse {
                    bound: false,
                    binding_id: req.binding_id.clone(),
                    error: Some(format!(
                        "Binding ID conflict: {} is already bound to another grant {}",
                        req.binding_id, existing_grant_id
                    )),
                };
            }
        }

        // Validate grant
        if let Err(err_msg) = self.validate_grant(&req.grant, current_time) {
            let _ = self.audit_logger.append(
                &self.node_id,
                &req.grant.space_id,
                "BIND_DENIED",
                &req.grant.grant_id,
                &req.device_id,
                &req.binding_id,
                &format!("DENIED: {}", err_msg),
                current_time,
            );
            return DeviceBindingResponse {
                bound: false,
                binding_id: req.binding_id,
                error: Some(err_msg),
            };
        }

        // Record active binding
        self.active_bindings.insert(req.binding_id.clone(), req.grant.grant_id.clone());

        let _ = self.audit_logger.append(
            &self.node_id,
            &req.grant.space_id,
            "DEVICE_BOUND",
            &req.grant.grant_id,
            &req.device_id,
            &req.binding_id,
            "SUCCESS",
            current_time,
        );

        DeviceBindingResponse {
            bound: true,
            binding_id: req.binding_id,
            error: None,
        }
    }

    pub fn release_device(&mut self, grant_id: &str, binding_id: &str, current_time: &str) -> bool {
        if let Some(bound_grant) = self.active_bindings.remove(binding_id) {
            let _ = self.audit_logger.append(
                &self.node_id,
                "global",
                "DEVICE_RELEASED",
                grant_id,
                "device-released",
                binding_id,
                "SUCCESS",
                current_time,
            );
            bound_grant == grant_id
        } else {
            false
        }
    }

    pub fn revoke_grant(&mut self, revocation_token: &str, current_time: &str) {
        if !self.revocations.contains(&revocation_token.to_string()) {
            self.revocations.push(revocation_token.to_string());
            let _ = self.audit_logger.append(
                &self.node_id,
                "global",
                "GRANT_REVOKED",
                "token-revocation",
                "all",
                revocation_token,
                "REVOKED",
                current_time,
            );
        }
    }

    pub fn health(&self) -> HealthReport {
        HealthReport {
            status: "ready".to_string(),
            cpu_percent: 5.0,
            memory_used_bytes: 512 * 1024 * 1024,
            memory_total_bytes: 16 * 1024 * 1024 * 1024,
            uptime_seconds: 3600,
            active_bindings_count: self.active_bindings.len(),
        }
    }
}
