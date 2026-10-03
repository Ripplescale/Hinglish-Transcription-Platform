//! Keep the Windows maximize button useful for a narrow notes window.
//!
//! This changes the native maximized bounds before layout, preserving Windows'
//! maximize/restore state and its normal restore rectangle. It deliberately does
//! not constrain the tracking size used by manual resizing and Snap.

use std::{cell::Cell, io, mem::size_of};
use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM},
    Graphics::Gdi::{
        GetMonitorInfoW, MonitorFromRect, MonitorFromWindow, HMONITOR, MONITORINFO,
        MONITOR_DEFAULTTONEAREST,
    },
    UI::{
        HiDpi::{GetDpiForWindow, GetSystemMetricsForDpi},
        Shell::{DefSubclassProc, GetWindowSubclass, RemoveWindowSubclass, SetWindowSubclass},
        WindowsAndMessaging::{
            GetWindowLongPtrW, GetWindowRect, IsIconic, IsZoomed, GWL_STYLE, MINMAXINFO,
            SM_CXPADDEDBORDER, SM_CYSIZEFRAME, SWP_NOMOVE, SWP_NOSIZE, WINDOWPOS, WM_GETMINMAXINFO,
            WM_NCDESTROY, WM_WINDOWPOSCHANGED, WM_WINDOWPOSCHANGING, WS_CAPTION, WS_THICKFRAME,
        },
    },
};

const SUBCLASS_ID: usize = 0x7878_766d;

#[derive(Clone, Copy)]
struct NormalBounds {
    rect: RECT,
    dpi: u32,
}

struct State {
    // All access is on the HWND's owning thread. Cell allows nested window
    // messages without holding an exclusive reference across a Win32 call.
    normal: Cell<NormalBounds>,
}

/// Install once on the thread that owns `hwnd`.
///
/// # Safety
/// `hwnd` must identify a live, decorated top-level window owned by this thread.
/// The subclass releases its state on WM_NCDESTROY; no external cleanup is needed.
pub unsafe fn install(hwnd: HWND) -> io::Result<()> {
    if hwnd.is_null() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Missing main window",
        ));
    }
    let mut existing = 0;
    if GetWindowSubclass(hwnd, Some(window_proc), SUBCLASS_ID, &mut existing) != 0 {
        return Ok(());
    }
    let mut rect = RECT::default();
    if GetWindowRect(hwnd, &mut rect) == 0 {
        return Err(io::Error::last_os_error());
    }
    let state = Box::into_raw(Box::new(State {
        normal: Cell::new(NormalBounds {
            rect,
            dpi: GetDpiForWindow(hwnd).max(1),
        }),
    }));
    if SetWindowSubclass(hwnd, Some(window_proc), SUBCLASS_ID, state as usize) == 0 {
        drop(Box::from_raw(state));
        return Err(io::Error::other("Could not install compact window layout"));
    }
    Ok(())
}

unsafe fn monitor_info(monitor: HMONITOR) -> Option<MONITORINFO> {
    let mut info = MONITORINFO {
        cbSize: size_of::<MONITORINFO>() as u32,
        ..Default::default()
    };
    (GetMonitorInfoW(monitor, &mut info) != 0).then_some(info)
}

/// Physical screen coordinates, including the standard maximized frame overhang.
fn maximized_rect(normal: NormalBounds, dpi: u32, work: RECT, frame_y: i32) -> Option<RECT> {
    let work_width = i64::from(work.right) - i64::from(work.left);
    let work_height = i64::from(work.bottom) - i64::from(work.top);
    let normal_width = i64::from(normal.rect.right) - i64::from(normal.rect.left);
    if work_width <= 0 || work_height <= 0 || normal_width <= 0 {
        return None;
    }
    // Keep the same logical width when Windows moves a maximized window between
    // monitors with different DPI. A normal move refreshes the physical cache.
    let denominator = i64::from(normal.dpi.max(1));
    let width = ((normal_width * i64::from(dpi.max(1)) + denominator / 2) / denominator)
        .clamp(1, work_width);
    let left =
        i64::from(normal.rect.left).clamp(i64::from(work.left), i64::from(work.right) - width);
    Some(RECT {
        left: left as i32,
        top: work.top.saturating_sub(frame_y.max(0)),
        right: (left + width) as i32,
        bottom: work.bottom.saturating_add(frame_y.max(0)),
    })
}

unsafe fn bounds_for_monitor(hwnd: HWND, normal: NormalBounds, info: &MONITORINFO) -> Option<RECT> {
    let dpi = GetDpiForWindow(hwnd).max(1);
    let style = GetWindowLongPtrW(hwnd, GWL_STYLE) as u32;
    let frame_y = if style & WS_THICKFRAME != 0 {
        GetSystemMetricsForDpi(SM_CYSIZEFRAME, dpi) + GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi)
    } else {
        0
    };
    maximized_rect(normal, dpi, info.rcWork, frame_y)
}

unsafe extern "system" fn window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    subclass_id: usize,
    reference: usize,
) -> LRESULT {
    let state = reference as *const State;
    match message {
        WM_NCDESTROY => {
            RemoveWindowSubclass(hwnd, Some(window_proc), subclass_id);
            drop(Box::from_raw(reference as *mut State));
            DefSubclassProc(hwnd, message, wparam, lparam)
        }
        WM_WINDOWPOSCHANGED => {
            // GetWindowRect already reflects the new position at this point.
            // Never replace the normal rectangle with maximized/minimized bounds.
            if IsZoomed(hwnd) == 0 && IsIconic(hwnd) == 0 {
                let mut rect = RECT::default();
                if GetWindowRect(hwnd, &mut rect) != 0 && rect.right > rect.left {
                    (*state).normal.set(NormalBounds {
                        rect,
                        dpi: GetDpiForWindow(hwnd).max(1),
                    });
                }
            }
            // Tao needs this chain to receive WM_SIZE and update the webview.
            DefSubclassProc(hwnd, message, wparam, lparam)
        }
        WM_GETMINMAXINFO => {
            let normal = (*state).normal.get();
            let result = DefSubclassProc(hwnd, message, wparam, lparam);
            if lparam == 0 || GetWindowLongPtrW(hwnd, GWL_STYLE) as u32 & WS_CAPTION == 0 {
                return result;
            }
            if let Some(info) = monitor_info(MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)) {
                if let Some(rect) = bounds_for_monitor(hwnd, normal, &info) {
                    let limits = &mut *(lparam as *mut MINMAXINFO);
                    limits.ptMaxPosition.x = rect.left.saturating_sub(info.rcMonitor.left);
                    limits.ptMaxPosition.y = rect.top.saturating_sub(info.rcMonitor.top);
                    limits.ptMaxSize.x = rect.right.saturating_sub(rect.left);
                    limits.ptMaxSize.y = rect.bottom.saturating_sub(rect.top);
                    // Leave both tracking sizes exactly as supplied by Windows
                    // and Tao, including the app's configured minimum size.
                    return 0;
                }
            }
            result
        }
        WM_WINDOWPOSCHANGING => {
            let normal = (*state).normal.get();
            let result = DefSubclassProc(hwnd, message, wparam, lparam);
            if lparam == 0
                || IsZoomed(hwnd) == 0
                || IsIconic(hwnd) != 0
                || GetWindowLongPtrW(hwnd, GWL_STYLE) as u32 & WS_CAPTION == 0
            {
                return result;
            }
            // Windows may compensate a custom MINMAXINFO size for differences
            // from the primary monitor, including both axes together. Correct
            // that pending native layout synchronously; never resize afterward.
            // https://devblogs.microsoft.com/oldnewthing/20150501-00/?p=44964
            let pending = *(lparam as *const WINDOWPOS);
            if pending.flags & (SWP_NOMOVE | SWP_NOSIZE) == (SWP_NOMOVE | SWP_NOSIZE) {
                return result;
            }
            let mut current = RECT::default();
            if GetWindowRect(hwnd, &mut current) == 0 {
                return result;
            }
            let left = if pending.flags & SWP_NOMOVE == 0 {
                pending.x
            } else {
                current.left
            };
            let top = if pending.flags & SWP_NOMOVE == 0 {
                pending.y
            } else {
                current.top
            };
            let width = if pending.flags & SWP_NOSIZE == 0 {
                pending.cx
            } else {
                current.right - current.left
            };
            let height = if pending.flags & SWP_NOSIZE == 0 {
                pending.cy
            } else {
                current.bottom - current.top
            };
            let proposed = RECT {
                left,
                top,
                right: left.saturating_add(width),
                bottom: top.saturating_add(height),
            };
            if let Some(info) = monitor_info(MonitorFromRect(&proposed, MONITOR_DEFAULTTONEAREST)) {
                if let Some(rect) = bounds_for_monitor(hwnd, normal, &info) {
                    let pending = &mut *(lparam as *mut WINDOWPOS);
                    pending.x = rect.left;
                    pending.y = rect.top;
                    pending.cx = rect.right.saturating_sub(rect.left);
                    pending.cy = rect.bottom.saturating_sub(rect.top);
                    pending.flags &= !(SWP_NOMOVE | SWP_NOSIZE);
                }
            }
            result
        }
        _ => DefSubclassProc(hwnd, message, wparam, lparam),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rect(left: i32, top: i32, right: i32, bottom: i32) -> RECT {
        RECT {
            left,
            top,
            right,
            bottom,
        }
    }
    fn normal(left: i32, width: i32, dpi: u32) -> NormalBounds {
        NormalBounds {
            rect: rect(left, 150, left + width, 700),
            dpi,
        }
    }
    fn coordinates(rect: RECT) -> (i32, i32, i32, i32) {
        (rect.left, rect.top, rect.right, rect.bottom)
    }

    #[test]
    fn preserves_compact_width_and_native_vertical_frame() {
        let result = maximized_rect(normal(350, 696, 96), 96, rect(0, 0, 1920, 1040), 8).unwrap();
        assert_eq!(coordinates(result), (350, -8, 1046, 1048));
    }

    #[test]
    fn respects_left_taskbar_and_negative_monitor_coordinates() {
        let result =
            maximized_rect(normal(-2000, 696, 96), 96, rect(-1870, -100, 0, 1300), 8).unwrap();
        assert_eq!(coordinates(result), (-1870, -108, -1174, 1308));
    }

    #[test]
    fn clamps_right_edge_and_an_oversized_restore_width() {
        let work = rect(1920, 40, 3200, 1024);
        assert_eq!(
            coordinates(maximized_rect(normal(3000, 696, 96), 96, work, 8).unwrap()),
            (2504, 32, 3200, 1032)
        );
        assert_eq!(
            coordinates(maximized_rect(normal(1900, 2000, 96), 96, work, 8).unwrap()),
            (1920, 32, 3200, 1032)
        );
    }

    #[test]
    fn preserves_logical_width_across_dpi_changes_without_drift() {
        let cached = normal(200, 696, 96);
        let work = rect(0, 0, 2560, 1400);
        let larger = maximized_rect(cached, 144, work, 12).unwrap();
        let restored = maximized_rect(cached, 96, work, 8).unwrap();
        assert_eq!(larger.right - larger.left, 1044);
        assert_eq!(restored.right - restored.left, 696);
    }

    #[test]
    fn rejects_unusable_rectangles() {
        assert!(maximized_rect(normal(0, 0, 96), 96, rect(0, 0, 1920, 1080), 8).is_none());
        assert!(maximized_rect(normal(0, 696, 96), 96, rect(0, 0, 0, 1080), 8).is_none());
    }
}
