#!/usr/bin/env python3
"""exp.py <apps/desktop-gpui dir>

Experiment build, applied after probe.py. Never landed. Lets one binary try
several launch orders:
  CAP_X_DEFER=dock,tray,menus   which steps move out of the launch closure
  CAP_X_WHEN=task|frame|frame2|ms:<n>   when the moved steps run
"""
import sys
from pathlib import Path

root = Path(sys.argv[1])
path = root / "src/main.rs"
text = path.read_text()


def replace_once(text, old, new):
    if text.count(old) != 1:
        sys.exit(f"expected exactly one match: {old!r}")
    return text.replace(old, new)


text = replace_once(
    text,
    '        platform::set_dock_icon(include_bytes!("../assets/dock-icon.png"));',
    """        let defer_spec = std::env::var("CAP_X_DEFER").unwrap_or_default();
        let deferred = |name: &str| defer_spec.split(',').any(|item| item == name);
        let mut later: Vec<Box<dyn FnOnce(&mut App)>> = Vec::new();
        if deferred("dock") {
            later.push(Box::new(|_: &mut App| {
                platform::set_dock_icon(include_bytes!("../assets/dock-icon.png"))
            }));
        } else {
            platform::set_dock_icon(include_bytes!("../assets/dock-icon.png"));
        }""",
)
text = replace_once(
    text,
    """        if !std::env::var("CAP_GPUI_NO_MENUS").is_ok_and(|v| v == "1") {
            menus::init(cx);
        }""",
    """        if !std::env::var("CAP_GPUI_NO_MENUS").is_ok_and(|v| v == "1") {
            if deferred("menus") {
                later.push(Box::new(menus::init));
            } else {
                menus::init(cx);
            }
        }""",
)
text = replace_once(
    text,
    """        if !std::env::var("CAP_GPUI_NO_TRAY").is_ok_and(|v| v == "1") {
            tray::init(cx);
        }""",
    """        if !std::env::var("CAP_GPUI_NO_TRAY").is_ok_and(|v| v == "1") {
            if deferred("tray") {
                later.push(Box::new(tray::init));
            } else {
                tray::init(cx);
            }
        }""",
)
text = replace_once(
    text,
    """        if !dev_restore::enabled() {
            cx.activate(true);
        }""",
    """        if !later.is_empty() {
            let when = std::env::var("CAP_X_WHEN").unwrap_or_default();
            let run = move |cx: &mut App| {
                tracing::info!("probe: deferred work start");
                for step in later {
                    step(cx);
                }
                tracing::info!("probe: deferred work done");
            };
            if when == "frame" {
                window_handle
                    .update(cx, |_, window, _| window.on_next_frame(move |_, cx| run(cx)))
                    .ok();
            } else if when == "frame2" {
                window_handle
                    .update(cx, |_, window, _| {
                        window.on_next_frame(move |window, _| {
                            window.on_next_frame(move |_, cx| run(cx))
                        })
                    })
                    .ok();
            } else if when == "commit" {
                let (committed, wait) = flume::bounded::<()>(1);
                let block = block2::RcBlock::new(move || {
                    let _ = committed.try_send(());
                });
                let _: () = unsafe {
                    objc2::msg_send![objc2::class!(CATransaction), setCompletionBlock: &*block]
                };
                cx.spawn(async move |cx| {
                    if wait.recv_async().await.is_ok() {
                        cx.update(run);
                    }
                })
                .detach();
            } else if let Some(ms) = when.strip_prefix("ms:").and_then(|v| v.parse::<u64>().ok()) {
                cx.spawn(async move |cx| {
                    cx.background_executor()
                        .timer(std::time::Duration::from_millis(ms))
                        .await;
                    cx.update(run);
                })
                .detach();
            } else {
                cx.spawn(async move |cx| {
                    cx.update(run);
                })
                .detach();
            }
        }
        if !dev_restore::enabled() {
            cx.activate(true);
        }""",
)
path.write_text(text)
print("experiment switches added")
