//! Enumerate active microphone sessions, without opening a recording stream.
//! Teams can own its session through a WebView child; follow only that child's
//! ancestry, never classify an unrelated browser as Teams.
use super::state::{app_for_process, CallApp};
use std::collections::BTreeSet;
use sysinfo::{Pid, ProcessRefreshKind, RefreshKind, System};
use windows::{
    core::Interface,
    Win32::{Media::Audio::*, System::Com::*},
};

pub struct AudioProbe;
impl AudioProbe {
    pub fn new() -> Result<Self, String> {
        unsafe {
            CoInitializeEx(None, COINIT_MULTITHREADED)
                .ok()
                .map_err(|e| e.to_string())?;
        }
        Ok(Self)
    }
    pub fn active_apps(&self) -> Result<BTreeSet<CallApp>, String> {
        unsafe { self.scan().map_err(|e| e.to_string()) }
    }
    unsafe fn scan(&self) -> windows::core::Result<BTreeSet<CallApp>> {
        let enumerator: IMMDeviceEnumerator =
            CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL)?;
        let devices = enumerator.EnumAudioEndpoints(eCapture, DEVICE_STATE_ACTIVE)?;
        let processes = System::new_with_specifics(
            RefreshKind::new().with_processes(ProcessRefreshKind::new()),
        );
        let mut found = BTreeSet::new();
        let mut readable = 0;
        for i in 0..devices.GetCount()? {
            // A device can disappear between enumeration and activation.
            let Ok(device) = devices.Item(i) else {
                continue;
            };
            let Ok(manager) = device.Activate::<IAudioSessionManager2>(CLSCTX_ALL, None) else {
                continue;
            };
            let Ok(sessions) = manager.GetSessionEnumerator() else {
                continue;
            };
            readable += 1;
            for j in 0..sessions.GetCount()? {
                let Ok(session) = sessions.GetSession(j) else {
                    continue;
                };
                if session.GetState()? != AudioSessionStateActive {
                    continue;
                }
                let control: IAudioSessionControl2 = session.cast()?;
                let mut pid = Pid::from_u32(control.GetProcessId()?);
                for depth in 0..8 {
                    let Some(process) = processes.process(pid) else {
                        break;
                    };
                    let name = process.name().to_string_lossy();
                    if let Some(app) = app_for_process(&name) {
                        found.insert(app);
                        break;
                    }
                    if depth == 0 && !name.eq_ignore_ascii_case("msedgewebview2.exe") {
                        break;
                    }
                    let Some(parent) = process.parent() else {
                        break;
                    };
                    if parent == pid {
                        break;
                    }
                    pid = parent;
                }
            }
        }
        if readable == 0 && devices.GetCount()? > 0 {
            return Err(windows::core::Error::from_hresult(windows::core::HRESULT(
                0x80004005u32 as i32,
            )));
        }
        Ok(found)
    }
}
impl Drop for AudioProbe {
    fn drop(&mut self) {
        unsafe {
            CoUninitialize();
        }
    }
}
