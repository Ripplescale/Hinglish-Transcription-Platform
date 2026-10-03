//! Detection metadata only: no audio samples, meeting titles, or participants.
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum CallApp {
    Zoom,
    Teams,
}
impl CallApp {
    pub fn label(self) -> &'static str {
        match self {
            Self::Zoom => "Zoom",
            Self::Teams => "Microsoft Teams",
        }
    }
}

pub fn app_for_process(name: &str) -> Option<CallApp> {
    match name.to_ascii_lowercase().as_str() {
        "zoom.exe" => Some(CallApp::Zoom),
        "ms-teams.exe" | "teams.exe" => Some(CallApp::Teams),
        _ => None,
    }
}

#[derive(Default)]
struct Seen {
    first: u64,
    last: u64,
    prompted: bool,
}

#[derive(Default)]
pub struct Detector {
    seen: BTreeMap<CallApp, Seen>,
}
impl Detector {
    /// Six seconds of sustained activity; thirty seconds absent to rearm.
    /// A recorded or dismissed call stays suppressed across short reconnects.
    pub fn update(
        &mut self,
        now: u64,
        active: &BTreeSet<CallApp>,
        recording: bool,
    ) -> Option<CallApp> {
        self.seen
            .retain(|_, seen| now.saturating_sub(seen.last) < 30);
        let mut next = None;
        for app in active {
            let seen = self.seen.entry(*app).or_insert(Seen {
                first: now,
                last: now,
                prompted: false,
            });
            if now.saturating_sub(seen.last) > 4 && !seen.prompted {
                seen.first = now;
            }
            seen.last = now;
            if recording {
                seen.prompted = true;
            }
            if !seen.prompted && now.saturating_sub(seen.first) >= 6 && next.is_none() {
                seen.prompted = true;
                next = Some(*app);
            }
        }
        next
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn active(app: CallApp) -> BTreeSet<CallApp> {
        [app].into_iter().collect()
    }
    #[test]
    fn debounce_and_reconnect() {
        let mut d = Detector::default();
        let a = active(CallApp::Zoom);
        for t in [0, 2, 4] {
            assert_eq!(d.update(t, &a, false), None);
        }
        assert_eq!(d.update(6, &a, false), Some(CallApp::Zoom));
        assert_eq!(d.update(8, &BTreeSet::new(), false), None);
        assert_eq!(d.update(20, &a, false), None);
        d.update(50, &BTreeSet::new(), false);
        for t in [52, 54, 56] {
            assert_eq!(d.update(t, &a, false), None);
        }
        assert_eq!(d.update(58, &a, false), Some(CallApp::Zoom));
    }
    #[test]
    fn transient_activity_does_not_prompt() {
        let mut d = Detector::default();
        let a = active(CallApp::Teams);
        d.update(0, &a, false);
        d.update(2, &BTreeSet::new(), false);
        assert_eq!(d.update(10, &a, false), None);
        assert_eq!(d.update(12, &a, false), None);
    }
    #[test]
    fn recording_suppresses_until_call_ends() {
        let mut d = Detector::default();
        let a = active(CallApp::Teams);
        d.update(0, &a, true);
        assert_eq!(d.update(6, &a, false), None);
    }
    #[test]
    fn apps_are_independent_and_exactly_matched() {
        assert_eq!(app_for_process("MS-Teams.exe"), Some(CallApp::Teams));
        assert_eq!(app_for_process("Teams.exe"), Some(CallApp::Teams));
        assert_eq!(app_for_process("Zoom.exe"), Some(CallApp::Zoom));
        assert_eq!(app_for_process("msedge.exe"), None);
        assert_eq!(app_for_process("not-zoom.exe"), None);
        let mut d = Detector::default();
        let a = [CallApp::Zoom, CallApp::Teams].into_iter().collect();
        for t in [0, 2, 4] {
            d.update(t, &a, false);
        }
        assert_eq!(d.update(6, &a, false), Some(CallApp::Zoom));
        assert_eq!(d.update(8, &a, false), Some(CallApp::Teams));
    }
}
