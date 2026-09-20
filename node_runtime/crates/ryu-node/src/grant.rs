//! Device-side grant validation and constant-time HMAC-SHA256 verification.
//! CONTRACT_MATRIX NODE-002, NODE-003, NODE-004 — Phase 7

use hmac::{Hmac, Mac};
use ryu_node_proto::NodeCapabilityGrant;
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

pub struct GrantVerifier;

impl GrantVerifier {
    /// Cryptographically verifies the HMAC signature of a NodeCapabilityGrant.
    pub fn verify_signature(grant: &NodeCapabilityGrant, shared_secret: &str) -> Result<(), String> {
        let mut mac = HmacSha256::new_from_slice(shared_secret.as_bytes())
            .map_err(|e| format!("HMAC init failed: {}", e))?;

        let canonical_bytes = grant.canonical_payload();
        mac.update(canonical_bytes.as_bytes());

        let expected_bytes = hex::decode(&grant.signature)
            .map_err(|_| "Signature is not valid hex".to_string())?;

        mac.verify_slice(&expected_bytes)
            .map_err(|_| "Cryptographic HMAC signature verification failed (forged or tampered grant)".to_string())
    }

    /// Complete device-side validation: authenticity, node matching, expiration, and revocation.
    pub fn validate_grant(
        grant: &NodeCapabilityGrant,
        expected_node_id: &str,
        shared_secret: &str,
        revocations: &[String],
        current_iso_time: &str,
    ) -> Result<(), String> {
        // 1. Authenticity check
        Self::verify_signature(grant, shared_secret)?;

        // 2. Node identity matching check (cross-node rejection)
        if grant.node_id != expected_node_id {
            return Err(format!(
                "Target node mismatch: grant issued for {}, current node is {}",
                grant.node_id, expected_node_id
            ));
        }

        // 3. Expiration check (string ISO comparison ISO 8601 lexicographical is valid for matching formats)
        if grant.expiry.as_str() <= current_iso_time {
            return Err(format!(
                "Grant expired at {}, current time is {}",
                grant.expiry, current_iso_time
            ));
        }

        // 4. Revocation blacklist check
        if revocations.contains(&grant.revocation_token) {
            return Err(format!(
                "Grant has been revoked (revocation token {})",
                grant.revocation_token
            ));
        }

        Ok(())
    }
}
