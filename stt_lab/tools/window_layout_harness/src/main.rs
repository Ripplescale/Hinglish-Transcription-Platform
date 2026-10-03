//! Native geometry regression for a disposable HWND, without Tauri, audio or user data.
//! Usage: cargo run --offline --manifest-path stt_lab/tools/window_layout_harness/Cargo.toml -- <report.json>
#![cfg(windows)]

include!(concat!(env!("OUT_DIR"), "/window_layout_link.rs"));

use serde_json::{json, Value};
use std::{
    mem::{size_of, zeroed},
    ptr::{null, null_mut},
    thread,
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM},
    Graphics::{
        Dwm::{DwmFlush, DwmGetWindowAttribute, DWMWA_EXTENDED_FRAME_BOUNDS},
        Gdi::{EnumDisplayMonitors, GetMonitorInfoW, HDC, HMONITOR, MONITORINFO},
    },
    System::LibraryLoader::GetModuleHandleW,
    UI::{
        HiDpi::{SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2},
        WindowsAndMessaging::*,
    },
};

unsafe extern "system" fn base_window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    let result = DefWindowProcW(hwnd, message, wparam, lparam);
    if message == WM_GETMINMAXINFO && lparam != 0 {
        let info = &mut *(lparam as *mut MINMAXINFO);
        info.ptMinTrackSize.x = 260;
        info.ptMinTrackSize.y = 220;
        info.ptMaxTrackSize.x = 8192;
        info.ptMaxTrackSize.y = 8192;
    }
    result
}

unsafe extern "system" fn collect_monitor(
    monitor: HMONITOR,
    _: HDC,
    _: *mut RECT,
    data: LPARAM,
) -> i32 {
    (*(data as *mut Vec<HMONITOR>)).push(monitor);
    1
}

fn rect_json(rect: RECT) -> Value {
    json!({ "left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom, "width": rect.right - rect.left, "height": rect.bottom - rect.top })
}
fn near(actual: i32, expected: i32) -> bool {
    (actual - expected).abs() <= 1
}
fn same_rect(left: RECT, right: RECT) -> bool {
    near(left.left, right.left)
        && near(left.top, right.top)
        && near(left.right, right.right)
        && near(left.bottom, right.bottom)
}

unsafe fn pump() {
    let until = Instant::now() + Duration::from_millis(130);
    let mut message: MSG = zeroed();
    while Instant::now() < until {
        while PeekMessageW(&mut message, null_mut(), 0, 0, PM_REMOVE) != 0 {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        thread::sleep(Duration::from_millis(5));
    }
    let _ = DwmFlush();
}

unsafe fn outer(hwnd: HWND) -> RECT {
    let mut rect = zeroed();
    assert_ne!(GetWindowRect(hwnd, &mut rect), 0);
    rect
}
unsafe fn frame(hwnd: HWND) -> RECT {
    let mut rect: RECT = zeroed();
    let result = DwmGetWindowAttribute(
        hwnd,
        DWMWA_EXTENDED_FRAME_BOUNDS as u32,
        &mut rect as *mut _ as *mut _,
        size_of::<RECT>() as u32,
    );
    assert_eq!(result, 0, "DWM frame geometry must be available");
    rect
}
unsafe fn snapshot(hwnd: HWND) -> Value {
    json!({ "outer": rect_json(outer(hwnd)), "dwm_frame": rect_json(frame(hwnd)), "zoomed": IsZoomed(hwnd) != 0, "minimized": IsIconic(hwnd) != 0 })
}

unsafe fn command(hwnd: HWND, value: u32) {
    SendMessageW(hwnd, WM_SYSCOMMAND, value as usize, 0);
    pump();
}
unsafe fn verify_expanded(
    hwnd: HWND,
    previous: RECT,
    work: RECT,
    checks: &mut Vec<Value>,
    label: &str,
) {
    let actual = frame(hwnd);
    let pass = IsZoomed(hwnd) != 0
        && IsIconic(hwnd) == 0
        && near(actual.left, previous.left)
        && near(actual.right, previous.right)
        && near(actual.top, work.top)
        && near(actual.bottom, work.bottom);
    checks.push(json!({ "check": label, "passed": pass, "before_dwm": rect_json(previous), "work_area": rect_json(work), "actual": snapshot(hwnd) }));
    assert!(
        pass,
        "{label}: maximize must retain visible width/x and exactly fill work-area height"
    );
}
unsafe fn verify_restored(hwnd: HWND, previous: RECT, checks: &mut Vec<Value>, label: &str) {
    let pass = IsZoomed(hwnd) == 0 && IsIconic(hwnd) == 0 && same_rect(outer(hwnd), previous);
    checks.push(json!({ "check": label, "passed": pass, "expected_outer": rect_json(previous), "actual": snapshot(hwnd) }));
    assert!(pass, "{label}: original normal placement must restore");
}

unsafe fn test_window(hwnd: HWND, work: RECT, checks: &mut Vec<Value>) {
    let initial = outer(hwnd);
    window_layout::install(hwnd).expect("Install exact application subclass");
    window_layout::install(hwnd).expect("Duplicate installation is safe");
    let mut mmi: MINMAXINFO = zeroed();
    SendMessageW(hwnd, WM_GETMINMAXINFO, 0, &mut mmi as *mut _ as isize);
    let tracking_preserved = mmi.ptMinTrackSize.x == 260
        && mmi.ptMinTrackSize.y == 220
        && mmi.ptMaxTrackSize.x == 8192
        && mmi.ptMaxTrackSize.y == 8192;
    checks.push(json!({ "check": "min_max_tracking_constraints_preserved", "passed": tracking_preserved, "minimum": [mmi.ptMinTrackSize.x, mmi.ptMinTrackSize.y], "maximum": [mmi.ptMaxTrackSize.x, mmi.ptMaxTrackSize.y] }));
    assert!(tracking_preserved);

    for index in 0..3 {
        let before = frame(hwnd);
        command(hwnd, SC_MAXIMIZE);
        verify_expanded(
            hwnd,
            before,
            work,
            checks,
            &format!("maximize_cycle_{}", index + 1),
        );
        command(hwnd, SC_RESTORE);
        verify_restored(
            hwnd,
            initial,
            checks,
            &format!("restore_cycle_{}", index + 1),
        );
    }

    for width in [600, 680, 820] {
        let width = width.min(work.right - work.left - 60);
        let height = 470.min(work.bottom - work.top - 70);
        let x = work.right - width - 30;
        assert_ne!(
            SetWindowPos(
                hwnd,
                null_mut(),
                x,
                work.top + 35,
                width,
                height,
                SWP_NOACTIVATE | SWP_NOZORDER
            ),
            0
        );
        pump();
        let before = outer(hwnd);
        let resized = before.right - before.left == width && before.left == x;
        checks.push(json!({ "check": format!("manual_resize_{}", width), "passed": resized, "actual": snapshot(hwnd) }));
        assert!(
            resized,
            "The subclass must not pin normal/snap-like resize width"
        );
        let before_frame = frame(hwnd);
        command(hwnd, SC_MAXIMIZE);
        verify_expanded(
            hwnd,
            before_frame,
            work,
            checks,
            &format!("resized_maximize_{}", width),
        );
        command(hwnd, SC_RESTORE);
        verify_restored(hwnd, before, checks, &format!("resized_restore_{}", width));
    }

    let before = outer(hwnd);
    command(hwnd, SC_MINIMIZE);
    assert_ne!(IsIconic(hwnd), 0);
    command(hwnd, SC_RESTORE);
    verify_restored(hwnd, before, checks, "normal_minimize_restore");

    let before_frame = frame(hwnd);
    command(hwnd, SC_MAXIMIZE);
    verify_expanded(
        hwnd,
        before_frame,
        work,
        checks,
        "before_maximized_minimize",
    );
    command(hwnd, SC_MINIMIZE);
    assert_ne!(IsIconic(hwnd), 0);
    command(hwnd, SC_RESTORE);
    verify_expanded(
        hwnd,
        before_frame,
        work,
        checks,
        "maximized_minimize_restore",
    );
    command(hwnd, SC_RESTORE);
    verify_restored(hwnd, before, checks, "final_restore");
}

fn main() {
    let report_path = std::env::args_os()
        .nth(1)
        .expect("Pass an output report.json path");
    let mut checks = Vec::new();
    let mut monitor_reports = Vec::new();
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| unsafe {
        assert_ne!(
            SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2),
            0
        );
        let instance = GetModuleHandleW(null());
        let class: Vec<u16> = "xxDisposableWindowLayoutQA\0".encode_utf16().collect();
        let title: Vec<u16> = "xx geometry check — temporary window\0"
            .encode_utf16()
            .collect();
        let window_class = WNDCLASSW {
            lpfnWndProc: Some(base_window_proc),
            hInstance: instance,
            lpszClassName: class.as_ptr(),
            ..zeroed()
        };
        assert_ne!(RegisterClassW(&window_class), 0);
        let mut monitors = Vec::new();
        assert_ne!(
            EnumDisplayMonitors(
                null_mut(),
                null(),
                Some(collect_monitor),
                &mut monitors as *mut _ as isize
            ),
            0
        );
        assert!(!monitors.is_empty());
        for (index, monitor) in monitors.into_iter().enumerate() {
            let mut info: MONITORINFO = zeroed();
            info.cbSize = size_of::<MONITORINFO>() as u32;
            assert_ne!(GetMonitorInfoW(monitor, &mut info), 0);
            monitor_reports.push(json!({ "index": index, "monitor": rect_json(info.rcMonitor), "work_area": rect_json(info.rcWork), "primary": info.dwFlags == 1 }));
            let width = 680.min(info.rcWork.right - info.rcWork.left - 60);
            let height = 560.min(info.rcWork.bottom - info.rcWork.top - 80);
            // A tool window cannot enter the user's Alt+Tab list or activate their work.
            let hwnd = CreateWindowExW(
                WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                class.as_ptr(),
                title.as_ptr(),
                WS_OVERLAPPEDWINDOW,
                info.rcWork.left + 30,
                info.rcWork.top + 40,
                width,
                height,
                null_mut(),
                null_mut(),
                instance,
                null(),
            );
            assert!(!hwnd.is_null());
            ShowWindow(hwnd, SW_SHOWNOACTIVATE);
            pump();
            let tested = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                test_window(hwnd, info.rcWork, &mut checks)
            }));
            assert_ne!(DestroyWindow(hwnd), 0);
            pump();
            assert_eq!(IsWindow(hwnd), 0);
            checks.push(json!({ "check": "destroy_subclassed_disposable_window", "passed": true, "monitor_index": index }));
            if let Err(error) = tested {
                std::panic::resume_unwind(error);
            }
        }
        assert_ne!(UnregisterClassW(class.as_ptr(), instance), 0);
    }));
    let error = result.as_ref().err().map(|payload| {
        payload
            .downcast_ref::<String>()
            .cloned()
            .or_else(|| {
                payload
                    .downcast_ref::<&str>()
                    .map(|value| value.to_string())
            })
            .unwrap_or_else(|| "Native geometry assertion failed".into())
    });
    let report = json!({ "passed": result.is_ok(), "exact_application_module": true, "tauri": false, "device_recording": false, "models_loaded": false, "user_app_windows_touched": false, "monitors": monitor_reports, "checks": checks, "error": error });
    std::fs::write(&report_path, serde_json::to_vec_pretty(&report).unwrap())
        .expect("Write report");
    println!("{}", serde_json::to_string(&report).unwrap());
    if result.is_err() {
        std::process::exit(1);
    }
}
