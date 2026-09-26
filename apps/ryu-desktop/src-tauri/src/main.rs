fn main() {
    let result = tauri::Builder::default()
        .run(tauri::generate_context!());
    if let Err(e) = result {
        eprintln!("Tauri error: {:?}", e);
        std::fs::write("tauri_error.log", format!("{:?}", e)).ok();
    }
}
