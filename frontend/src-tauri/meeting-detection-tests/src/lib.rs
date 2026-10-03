//! Compile the actual native integration without loading transcription models.
//! Only existing app boundaries are stubbed; detector and Tauri commands are real.
#[path = "../../src/meeting_detection/mod.rs"]
mod meeting_detection;
pub fn configure(builder: tauri::Builder<tauri::Wry>) -> tauri::Builder<tauri::Wry> {
    builder.invoke_handler(tauri::generate_handler![
        meeting_detection::meeting_detection_status,
        meeting_detection::set_meeting_detection,
        meeting_detection::meeting_detection_ready,
        meeting_detection::meeting_detection_take_request,
        meeting_detection::meeting_prompt,
        meeting_detection::respond_to_meeting,
    ])
}
mod audio {
    pub mod recording_commands {
        pub fn get_active_capture() -> Result<Option<()>, String> {
            Ok(None)
        }
        pub async fn is_recording() -> bool {
            false
        }
    }
}
mod tray {
    pub fn focus_main_window(_: &tauri::AppHandle) {}
}
