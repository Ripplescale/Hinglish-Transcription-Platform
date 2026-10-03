#[path = "../../src/meeting_detection/state.rs"]
mod state;
#[cfg(windows)]
#[path = "../../src/meeting_detection/windows.rs"]
mod windows;
fn main() {
    #[cfg(windows)]
    {
        let probe =
            windows::AudioProbe::new().expect("Initialize Windows audio session inspection");
        let active = probe.active_apps().expect("Enumerate microphone sessions");
        println!("Active call apps: {:?}", active);
    }
}
