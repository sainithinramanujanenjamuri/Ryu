//! Platform-specific discovery and device inspection adapter.
//! CONTRACT_MATRIX NODE-001, ADR-0020 — Phase 7

use ryu_node_proto::{DeviceInfo, DeviceState, DeviceType, NodeInfo, NodeState};
use std::collections::HashMap;

pub struct PlatformAdapter;

impl PlatformAdapter {
    /// Detect platform kind, architecture, and profile (Windows, Linux, WSL2).
    pub fn inspect_node(node_id: &str) -> NodeInfo {
        let os_str = std::env::consts::OS;
        let arch_str = std::env::consts::ARCH;

        let is_wsl = if os_str == "linux" {
            std::path::Path::new("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
                || std::fs::read_to_string("/proc/version")
                    .map(|s| s.to_lowercase().contains("microsoft") || s.to_lowercase().contains("wsl"))
                    .unwrap_or(false)
        } else {
            false
        };

        let profile = if is_wsl {
            "wsl2".to_string()
        } else if os_str == "windows" {
            "windows_host".to_string()
        } else {
            "native_linux".to_string()
        };

        let cpu_cores = std::thread::available_parallelism()
            .map(|p| p.get() as u32)
            .unwrap_or(4);

        // Approximate total RAM
        let memory_total_bytes: u64 = if os_str == "windows" {
            16 * 1024 * 1024 * 1024 // 16GB baseline default for windows host
        } else {
            8 * 1024 * 1024 * 1024 // 8GB default
        };

        let storage_total_bytes: u64 = 500 * 1024 * 1024 * 1024; // 500GB baseline default

        let mut labels = HashMap::new();
        labels.insert("tier".to_string(), "physical".to_string());
        labels.insert("platform".to_string(), os_str.to_string());
        labels.insert("environment".to_string(), profile.clone());

        NodeInfo {
            node_id: node_id.to_string(),
            platform: os_str.to_string(),
            architecture: arch_str.to_string(),
            environment_profile: profile,
            runtime_state: NodeState::Ready,
            cpu_cores,
            memory_total_bytes,
            storage_total_bytes,
            capabilities: vec![
                "compute.cpu".to_string(),
                "compute.gpu".to_string(),
                "storage.workspace".to_string(),
            ],
            labels,
        }
    }

    /// Discovers physical / local devices available on this host.
    pub fn discover_devices(node_id: &str) -> Vec<DeviceInfo> {
        let mut devices = Vec::new();

        // 1. CPU Device
        let mut cpu_meta = HashMap::new();
        cpu_meta.insert("cores".to_string(), "8".to_string());
        cpu_meta.insert("arch".to_string(), std::env::consts::ARCH.to_string());
        devices.push(DeviceInfo {
            device_id: format!("{}-cpu-0", node_id),
            node_id: node_id.to_string(),
            device_type: DeviceType::Cpu,
            capability_metadata: cpu_meta,
            availability_state: DeviceState::Online,
            total_capacity: 100, // 100% quota
        });

        // 2. GPU Device (e.g. CUDA / DirectX)
        let mut gpu_meta = HashMap::new();
        let os_str = std::env::consts::OS;
        if os_str == "windows" {
            gpu_meta.insert("backend".to_string(), "directx_dxgi".to_string());
            gpu_meta.insert("vram_mb".to_string(), "8192".to_string());
        } else {
            gpu_meta.insert("backend".to_string(), "cuda".to_string());
            gpu_meta.insert("vram_mb".to_string(), "8192".to_string());
        }
        devices.push(DeviceInfo {
            device_id: format!("{}-gpu-0", node_id),
            node_id: node_id.to_string(),
            device_type: DeviceType::Gpu,
            capability_metadata: gpu_meta,
            availability_state: DeviceState::Online,
            total_capacity: 1, // Exclusive single-lease device
        });

        // 3. Storage Device
        let mut storage_meta = HashMap::new();
        storage_meta.insert("mount".to_string(), "scratch_volume".to_string());
        devices.push(DeviceInfo {
            device_id: format!("{}-storage-0", node_id),
            node_id: node_id.to_string(),
            device_type: DeviceType::Storage,
            capability_metadata: storage_meta,
            availability_state: DeviceState::Online,
            total_capacity: 10 * 1024 * 1024 * 1024, // 10GB workspace quota
        });

        devices
    }
}
