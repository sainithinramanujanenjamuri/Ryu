//! ryu-node-proto: Node Runtime Protocol Contract Types
//! Space-Centric Cognitive Architecture (SCCA)

pub mod generated;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RiskTier {
    Low,
    High,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeCapabilityGrant {
    pub node_id: String,
    pub space_id: String,
    pub capability: String,
    pub risk_tier: RiskTier,
    pub approver_id: String,
    pub expiry: String,
    pub revocation_token: String,
}
