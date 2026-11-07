#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{WebviewUrl, WebviewWindowBuilder};

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            // This window has no Tauri commands or remote capabilities. The
            // Python launcher starts an isolated server before opening it.
            let url = "http://127.0.0.1:18765/".parse()?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("合拍 Cojam · Tauri 实验版")
                .inner_size(1280.0, 860.0)
                .min_inner_size(960.0, 640.0)
                .on_navigation(|url| {
                    url.scheme() == "http"
                        && url.host_str() == Some("127.0.0.1")
                        && url.port_or_known_default() == Some(18765)
                })
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("Tauri 实验窗口启动失败");
}
