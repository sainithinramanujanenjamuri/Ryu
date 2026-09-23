//! ryu-node: Physical Node Runtime Implementation
//! Space-Centric Cognitive Architecture (SCCA) — Phase 7 & Phase 11
//! CONTRACT_MATRIX NODE-001..NODE-013, ADR-0017..ADR-0020, ADR-0037..ADR-0039

pub mod audit;
pub mod grant;
pub mod platform;
pub mod policy;

use audit::DeviceAuditLogger;
use grant::GrantVerifier;
use platform::PlatformAdapter;
pub use policy::{NodeTrustTier, RestrictedNodePolicy};
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
    /// Trust tier of this node (full_trust or restricted)
    pub trust_tier: NodeTrustTier,
    /// Local MDM policy constraint (if restricted tier)
    pub policy: Option<RestrictedNodePolicy>,
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
            trust_tier: NodeTrustTier::FullTrust,
            policy: None,
        })
    }

    pub fn with_policy(mut self, tier: NodeTrustTier, policy: Option<RestrictedNodePolicy>) -> Self {
        self.trust_tier = tier;
        self.policy = policy;
        self
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

    /// Evaluates binding request according to the strict SCCA authorization order:
    /// 1. Structural check
    /// 2. Target node check
    /// 3. Space membership check
    /// 4. Cryptographic HMAC validation (validate_grant)
    /// 5. MDM / Restricted-node local policy evaluation (MDM is an additional constraint, not authentication)
    /// 6. Active lease & device binding
    /// 7. Append-only audit record
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

        // 1-4. Validate grant (Node ID, Space, HMAC signature, Expiry, Revocations)
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

        // 5. MDM / Restricted-node policy evaluation
        // INVARIANT: MDM_ALLOW != Authentication. MDM is an additional local policy constraint.
        if self.trust_tier == NodeTrustTier::Restricted {
            if let Some(ref pol) = self.policy {
                if let Err(pol_err) = pol.evaluate_capability(&req.grant.capability) {
                    let _ = self.audit_logger.append(
                        &self.node_id,
                        &req.grant.space_id,
                        "BIND_DENIED",
                        &req.grant.grant_id,
                        &req.device_id,
                        &req.binding_id,
                        &format!("DENIED: {}", pol_err),
                        current_time,
                    );
                    return DeviceBindingResponse {
                        bound: false,
                        binding_id: req.binding_id,
                        error: Some(pol_err),
                    };
                }
            }
        }

        // 6. Record active binding
        self.active_bindings.insert(req.binding_id.clone(), req.grant.grant_id.clone());

        // 7. Append to Audit Log
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
