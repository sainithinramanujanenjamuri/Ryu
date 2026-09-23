//! ryu-node CLI binary: Physical Node Runtime boundary
//! CONTRACT_MATRIX NODE-001, NODE-002, NODE-008, NODE-012 — Phase 7 & 11

use ryu_node::audit::DeviceAuditLogger;
use ryu_node::platform::PlatformAdapter;
use ryu_node::policy::{NodeTrustTier, RestrictedNodePolicy};
use ryu_node::NodeRuntime;
use ryu_node_proto::{DeviceBindingRequest, NodeCapabilityGrant};
use std::env;

fn print_usage() {
    eprintln!("Usage: ryu-node <subcommand> [args...]");
    eprintln!("Subcommands:");
    eprintln!("  inspect --node-id <id>");
    eprintln!("  inspect-devices --node-id <id>");
    eprintln!("  validate-grant --node-id <id> --secret <key> --grant <json> [--time <iso>]");
    eprintln!("  bind --node-id <id> --secret <key> --audit-log <path> --req <json> [--trust-tier <tier>] [--policy <json>] [--time <iso>]");
    eprintln!("  release --node-id <id> --audit-log <path> --grant-id <id> --binding-id <id> [--time <iso>]");
    eprintln!("  health");
    eprintln!("  audit-verify --log-path <path>");
    eprintln!("  version");
}

fn get_arg(args: &[String], flag: &str) -> Option<String> {
    for i in 0..args.len() {
        if args[i] == flag && i + 1 < args.len() {
            return Some(args[i + 1].clone());
        }
    }
    None
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        print_usage();
        std::process::exit(1);
    }

    let subcmd = &args[1];
    match subcmd.as_str() {
        "version" => {
            println!("ryu-node v{}", ryu_node::version());
        }
        "inspect" => {
            let node_id = get_arg(&args, "--node-id").unwrap_or_else(|| "default-node".to_string());
            let info = PlatformAdapter::inspect_node(&node_id);
            println!("{}", serde_json::to_string(&info).unwrap());
        }
        "inspect-devices" => {
            let node_id = get_arg(&args, "--node-id").unwrap_or_else(|| "default-node".to_string());
            let devices = PlatformAdapter::discover_devices(&node_id);
            println!("{}", serde_json::to_string(&devices).unwrap());
        }
        "validate-grant" => {
            let node_id = get_arg(&args, "--node-id").unwrap_or_else(|| "default-node".to_string());
            let secret = get_arg(&args, "--secret").unwrap_or_default();
            let grant_json = get_arg(&args, "--grant").unwrap_or_default();
            let current_time = get_arg(&args, "--time").unwrap_or_else(|| "2026-09-20T12:00:00Z".to_string());

            let grant: NodeCapabilityGrant = match serde_json::from_str(&grant_json) {
                Ok(g) => g,
                Err(e) => {
                    println!(
                        "{}",
                        serde_json::json!({
                            "valid": false,
                            "error": format!("Malformed grant JSON: {}", e)
                        })
                    );
                    return;
                }
            };

            let runtime = NodeRuntime::new(&node_id, &secret, "audit.temp.log").unwrap();
            match runtime.validate_grant(&grant, &current_time) {
                Ok(()) => {
                    println!("{}", serde_json::json!({ "valid": true, "error": null }));
                }
                Err(err) => {
                    println!("{}", serde_json::json!({ "valid": false, "error": err }));
                }
            }
        }
        "bind" => {
            let node_id = get_arg(&args, "--node-id").unwrap_or_else(|| "default-node".to_string());
            let secret = get_arg(&args, "--secret").unwrap_or_default();
            let audit_log = get_arg(&args, "--audit-log").unwrap_or_else(|| "audit.log.jsonl".to_string());
            let req_json = get_arg(&args, "--req").unwrap_or_default();
            let current_time = get_arg(&args, "--time").unwrap_or_else(|| "2026-09-20T12:00:00Z".to_string());
            let tier_str = get_arg(&args, "--trust-tier").unwrap_or_else(|| "full_trust".to_string());
            let policy_json = get_arg(&args, "--policy");

            let req: DeviceBindingRequest = match serde_json::from_str(&req_json) {
                Ok(r) => r,
                Err(e) => {
                    println!(
                        "{}",
                        serde_json::json!({
                            "bound": false,
                            "binding_id": "none",
                            "error": format!("Malformed binding request JSON: {}", e)
                        })
                    );
                    return;
                }
            };

            let mut runtime = match NodeRuntime::new(&node_id, &secret, &audit_log) {
                Ok(r) => r,
                Err(e) => {
                    println!(
                        "{}",
                        serde_json::json!({
                            "bound": false,
                            "binding_id": req.binding_id,
                            "error": format!("Node runtime init failed: {}", e)
                        })
                    );
                    return;
                }
            };

            let trust_tier = if tier_str == "restricted" {
                NodeTrustTier::Restricted
            } else {
                NodeTrustTier::FullTrust
            };

            let policy: Option<RestrictedNodePolicy> = policy_json.and_then(|p| serde_json::from_str(&p).ok());
            runtime = runtime.with_policy(trust_tier, policy);

            let resp = runtime.bind_device(req, &current_time);
            println!("{}", serde_json::to_string(&resp).unwrap());
        }
        "release" => {
            let node_id = get_arg(&args, "--node-id").unwrap_or_else(|| "default-node".to_string());
            let audit_log = get_arg(&args, "--audit-log").unwrap_or_else(|| "audit.log.jsonl".to_string());
            let grant_id = get_arg(&args, "--grant-id").unwrap_or_default();
            let binding_id = get_arg(&args, "--binding-id").unwrap_or_default();
            let current_time = get_arg(&args, "--time").unwrap_or_else(|| "2026-09-20T12:00:00Z".to_string());

            let mut runtime = NodeRuntime::new(&node_id, "secret", &audit_log).unwrap();
            let released = runtime.release_device(&grant_id, &binding_id, &current_time);
            println!("{}", serde_json::json!({ "released": released }));
        }
        "health" => {
            let runtime = NodeRuntime::new("node-01", "secret", "audit.temp.log").unwrap();
            let h = runtime.health();
            println!("{}", serde_json::to_string(&h).unwrap());
        }
        "audit-verify" => {
            let log_path = get_arg(&args, "--log-path").unwrap_or_else(|| "audit.log.jsonl".to_string());
            match DeviceAuditLogger::verify_file(&log_path) {
                Ok(count) => {
                    println!(
                        "{}",
                        serde_json::json!({
                            "verified": true,
                            "count": count,
                            "error": null
                        })
                    );
                }
                Err(err) => {
                    println!(
                        "{}",
                        serde_json::json!({
                            "verified": false,
                            "count": 0,
                            "error": err
                        })
                    );
                }
            }
        }
        other => {
            eprintln!("Unknown subcommand: {}", other);
            print_usage();
            std::process::exit(1);
        }
    }
}
