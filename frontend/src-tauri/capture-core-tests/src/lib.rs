#[path = "../../src/audio/durable_capture.rs"]
pub mod durable_capture;

#[allow(dead_code)]
mod workspace_components {
    include!(concat!(env!("OUT_DIR"), "/workspace_components.rs"));
}

#[cfg(test)]
mod worker_lifecycle {
    use serde_json::{json, Value};
    include!(concat!(env!("OUT_DIR"), "/worker_lifecycle.rs"));

    #[test]
    fn terminal_checkpoint_does_not_release_a_running_process_slot() {
        for state in ["complete", "failed", "stopped"] {
            let mut status = json!({"state":state,"segments":[{"text":"preserved"}]});
            project_worker_lifecycle(&mut status, false);
            assert_eq!(status["state"], "running");
            assert_eq!(status["checkpoint_state"], state);
            assert_eq!(status["segments"][0]["text"], "preserved");
            let mut exited = json!({"state":state});
            project_worker_lifecycle(&mut exited, true);
            assert_eq!(exited["state"], state);
        }
        let mut waiting = json!({"state":"waiting_for_audio"});
        project_worker_lifecycle(&mut waiting, false);
        assert_eq!(waiting["state"], "waiting_for_audio");
    }
}

#[cfg(test)]
mod automatic_workflow {
    use serde_json::{json, Value};
    use std::{fs, path::Path};
    include!(concat!(env!("OUT_DIR"), "/automatic_workflow.rs"));
}

#[cfg(test)]
mod syntax_checks {
    /// Syntax coverage only: this deliberately does not claim a Tauri typecheck.
    #[test]
    fn modified_application_sources_parse() {
        for (name, source) in [
            ("recording_commands", include_str!("../../src/audio/recording_commands.rs")),
            ("recording_manager", include_str!("../../src/audio/recording_manager.rs")),
            ("recording_state", include_str!("../../src/audio/recording_state.rs")),
            ("recording_saver", include_str!("../../src/audio/recording_saver.rs")),
            ("stream", include_str!("../../src/audio/stream.rs")),
            ("device_monitor", include_str!("../../src/audio/device_monitor.rs")),
            ("pipeline", include_str!("../../src/audio/pipeline.rs")),
            ("local_workspace", include_str!("../../src/local_workspace.rs")),
            ("local_transcription", include_str!("../../src/local_transcription.rs")),
            ("lib", include_str!("../../src/lib.rs")),
        ] {
            syn::parse_file(source).unwrap_or_else(|error| panic!("{name}: {error}"));
        }
    }
}
