//! Device-side MDM Policy Engine.
//! CONTRACT_MATRIX NODE-012, ADR-0039 — Phase 11
//!
//! INVARIANT:
//! MDM_ALLOW != Authentication.
//! MDM is an additional local policy constraint, not an authority root.
//! MDM_ALLOW alone is NOT sufficient.
//! valid_DeviceGrant alone is NOT sufficient on a Restricted node if MDM denies.
//! MDM_DENY -> binding denied.

use serde::{Deserialize, Serialize};
use std::collections::HashSet;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum NodeTrustTier {
    FullTrust,
    Restricted,
}

impl Default for NodeTrustTier {
    fn default() -> Self {
        NodeTrustTier::FullTrust
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RestrictedNodePolicy {
    pub policy_id: String,
    #[serde(default)]
    pub allowed_capabilities: HashSet<String>,
    #[serde(default)]
    pub denied_capabilities: HashSet<String>,
    #[serde(default)]
    pub allowed_storage_paths: Vec<String>,
    #[serde(default)]
    pub allow_terminal_exec: bool,
}

impl RestrictedNodePolicy {
    pub fn new(policy_id: &str) -> Self {
        Self {
            policy_id: policy_id.to_string(),
            allowed_capabilities: HashSet::new(),
            denied_capabilities: HashSet::new(),
            allowed_storage_paths: Vec::new(),
            allow_terminal_exec: false,
        }
    }

    /// Evaluates whether a requested capability satisfies the local MDM allow-list / deny-list.
    /// Returns Ok(()) if permitted, or Err(reason) if rejected.
    pub fn evaluate_capability(&self, capability: &str) -> Result<(), String> {
        if self.denied_capabilities.contains(capability) {
            return Err(format!(
                "MDM policy violation: capability '{}' is explicitly denied by policy '{}'",
                capability, self.policy_id
            ));
        }

        if !self.allowed_capabilities.is_empty() && !self.allowed_capabilities.contains(capability) {
            return Err(format!(
                "MDM policy violation: capability '{}' is not in allow-list for policy '{}'",
                capability, self.policy_id
            ));
        }

        Ok(())
    }
}

