"""by-harness.py <repo root>: BY's measurement logs for the hx (harness) build. Scratch, never landed.
1. meetings/mod.rs: one `by line` per caption/final the app receives (wall ms, end ms, final, source),
   and `by origin` with the capture origin's wall clock, so caption lag = wall - (origin + end).
2. meetings/dev.rs: `by stop` when the auto meeting presses Stop; `by notes` per notes stage.
3. documents/dev.rs: CAP_BY_FRAMES=1 samples the notes window's frames (GPUI frame trace) during a
   live meeting, one `by frames` line per 30 s (count, p50, p95, max, over 8.3 / 16.7 ms)."""
import sys

root = sys.argv[1] + "/apps/desktop-gpui/src"


def patch(path, anchor, add, before=False):
    p = f"{root}/{path}"
    s = open(p).read()
    assert s.count(anchor) == 1, (path, anchor, s.count(anchor))
    s = s.replace(anchor, (add + anchor) if before else (anchor + add))
    open(p, "w").write(s)


EPOCH = "std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_millis() as u64)"

patch(
    "meetings/mod.rs",
    "                let id = utterance.id.clone();\n",
    f'                tracing::info!(target: "by_line", fin = is_final, src = ?source, end = end_ms, wall = {EPOCH}, "by line");\n',
)
patch(
    "meetings/mod.rs",
    "                meeting.started_at = Some(capture.origin_wall());\n",
    '                tracing::info!(target: "by_origin", wall = capture.origin_wall().timestamp_millis(), "by origin");\n',
)
patch(
    "meetings/dev.rs",
    "    meetings.update(cx, |m, cx| m.enable_in_memory(cx));\n",
    """    cx.subscribe(&meetings, |_, event: &super::MeetingEvent, _| {
        if let super::MeetingEvent::NotesProgress { stage, .. } = event {
            let name = match stage {
                super::NotesStage::Started => "started",
                super::NotesStage::Title(_) => "title",
                super::NotesStage::Done => "done",
                super::NotesStage::Failed(_) => "failed",
                _ => return,
            };
            tracing::info!(target: "by_notes", stage = name, wall = epoch_ms() as u64, "by notes");
        }
    })
    .detach();
""",
)
patch(
    "meetings/dev.rs",
    "            cx.update(|cx| meetings.update(cx, |m, cx| m.stop_recording(StopReason::User, cx)));\n",
    '            tracing::info!(target: "by_stop", wall = epoch_ms() as u64, "by stop");\n',
    before=True,
)
patch(
    "meetings_ui/documents/dev.rs",
    "            super::open(Some(id), cx);\n",
    """            if std::env::var_os("CAP_BY_FRAMES").is_some() {
                cx.spawn(async move |cx| {
                    sleep(cx, 3000).await;
                    gpui::profiler::set_frame_trace_enabled(true);
                    let mut collector = gpui::profiler::FrameTimingCollector::new();
                    let mut ms: Vec<f64> = Vec::new();
                    let mut ticks = 0u64;
                    loop {
                        sleep(cx, 1000).await;
                        ticks += 1;
                        let wid = cx.update(|cx| window(cx).map(|h| h.window_id()));
                        for f in collector.collect_unseen() {
                            if Some(f.window_id) == wid {
                                ms.push(f.draw_duration().as_secs_f64() * 1e3);
                            }
                        }
                        if ticks % 30 == 0 {
                            let mut s = std::mem::take(&mut ms);
                            s.sort_by(|a, b| a.total_cmp(b));
                            let pct = |p: f64| if s.is_empty() { 0.0 } else { s[((s.len() - 1) as f64 * p).round() as usize] };
                            let over8 = s.iter().filter(|v| **v > 8.33).count();
                            let over16 = s.iter().filter(|v| **v > 16.67).count();
                            tracing::info!(target: "by_frames", t = ticks, n = s.len(), p50 = pct(0.5), p95 = pct(0.95), max = pct(1.0), over8, over16, "by frames");
                        }
                    }
                })
                .detach();
            }
""",
)
print("by-harness: applied")
