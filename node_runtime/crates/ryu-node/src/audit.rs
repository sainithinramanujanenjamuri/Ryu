//! Local append-only audit logger with SHA-256 hash chaining.
//! CONTRACT_MATRIX NODE-008 — Phase 7

use ryu_node_proto::AuditEntry;
use sha2::{Digest, Sha256};
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::Path;

pub const GENESIS_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

pub struct DeviceAuditLogger {
    log_path: String,
    last_seq: u64,
    last_hash: String,
}

impl DeviceAuditLogger {
    pub fn open(log_path: &str) -> Result<Self, String> {
        let path = Path::new(log_path);
        let mut last_seq = 0;
        let mut last_hash = GENESIS_HASH.to_string();

        if path.exists() {
            let file = File::open(path).map_err(|e| e.to_string())?;
            let reader = BufReader::new(file);
            for line in reader.lines() {
                let line_str = line.map_err(|e| e.to_string())?;
                if line_str.trim().is_empty() {
                    continue;
                }
                let entry: AuditEntry =
                    serde_json::from_str(&line_str).map_err(|e| format!("Corrupt audit entry: {}", e))?;
                last_seq = entry.seq;
                last_hash = entry.record_hash;
            }
        }

        Ok(Self {
            log_path: log_path.to_string(),
            last_seq,
            last_hash,
        })
    }

    pub fn append(
        &mut self,
        node_id: &str,
        space_id: &str,
        event_type: &str,
        grant_id: &str,
        device_id: &str,
        operation_id: &str,
        result: &str,
        timestamp: &str,
    ) -> Result<AuditEntry, String> {
        let seq = self.last_seq + 1;
        let prev_hash = self.last_hash.clone();

        let raw_payload = format!(
            "{}|{}|{}|{}|{}|{}|{}|{}|{}|{}",
            seq,
            timestamp,
            node_id,
            space_id,
            event_type,
            grant_id,
            device_id,
            operation_id,
            result,
            prev_hash
        );

        let mut hasher = Sha256::new();
        hasher.update(raw_payload.as_bytes());
        let record_hash = hex::encode(hasher.finalize());

        let entry = AuditEntry {
            seq,
            timestamp: timestamp.to_string(),
            node_id: node_id.to_string(),
            space_id: space_id.to_string(),
            event_type: event_type.to_string(),
            grant_id: grant_id.to_string(),
            device_id: device_id.to_string(),
            operation_id: operation_id.to_string(),
            result: result.to_string(),
            prev_hash,
            record_hash: record_hash.clone(),
        };

        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.log_path)
            .map_err(|e| e.to_string())?;

        let json_line = serde_json::to_string(&entry).map_err(|e| e.to_string())?;
        writeln!(file, "{}", json_line).map_err(|e| e.to_string())?;

        self.last_seq = seq;
        self.last_hash = record_hash;

        Ok(entry)
    }

    /// Verifies the full SHA-256 hash chain in an audit file.
    pub fn verify_file(log_path: &str) -> Result<u64, String> {
        let path = Path::new(log_path);
        if !path.exists() {
            return Ok(0);
        }

        let file = File::open(path).map_err(|e| e.to_string())?;
        let reader = BufReader::new(file);

        let mut expected_prev_hash = GENESIS_HASH.to_string();
        let mut count: u64 = 0;

        for (idx, line) in reader.lines().enumerate() {
            let line_str = line.map_err(|e| e.to_string())?;
            if line_str.trim().is_empty() {
                continue;
            }
            let entry: AuditEntry =
                serde_json::from_str(&line_str).map_err(|e| format!("Invalid JSON line {}: {}", idx + 1, e))?;

            if entry.prev_hash != expected_prev_hash {
                return Err(format!(
                    "Audit chain broken at seq {}: prev_hash mismatch (expected {}, got {})",
                    entry.seq, expected_prev_hash, entry.prev_hash
                ));
            }

            let raw_payload = format!(
                "{}|{}|{}|{}|{}|{}|{}|{}|{}|{}",
                entry.seq,
                entry.timestamp,
                entry.node_id,
                entry.space_id,
                entry.event_type,
                entry.grant_id,
                entry.device_id,
                entry.operation_id,
                entry.result,
                entry.prev_hash
            );

            let mut hasher = Sha256::new();
            hasher.update(raw_payload.as_bytes());
            let computed_hash = hex::encode(hasher.finalize());

            if computed_hash != entry.record_hash {
                return Err(format!(
                    "Audit record hash corruption at seq {}: computed {}, stored {}",
                    entry.seq, computed_hash, entry.record_hash
                ));
            }

            expected_prev_hash = entry.record_hash;
            count += 1;
        }

        Ok(count)
    }
}
