#!/usr/bin/env python3
"""probe.py <apps/desktop-gpui dir>

Measurement-only build: adds `probe: <phase>` log lines through launch so the
timestamps give a per-phase timeline. Never landed.
"""
import sys
from pathlib import Path

root = Path(sys.argv[1])


def insert_after(text, anchor, addition, count=1):
    index = text.find(anchor)
    if index < 0:
        sys.exit(f"anchor not found: {anchor!r}")
    if count == 1 and text.find(anchor, index + 1) >= 0:
        sys.exit(f"anchor not unique: {anchor!r}")
    end = index + len(anchor)
    return text[:end] + addition + text[end:]


def probe(name, indent):
    return f'\n{" " * indent}tracing::info!("probe: {name}");'


main = (root / "src/main.rs").read_text()
steps = [
    ("    startup::begin(init_logging());", "logging", 4),
    ("    crash_capture::install(&diagnostics::logs_dir());", "crash_capture::install", 4),
    ("    single_instance::acquire();", "single_instance::acquire", 4),
    ("    let previous_session = crash_capture::begin_session(&diagnostics::logs_dir());", "begin_session", 4),
    ("    blur_recovery::configure(previous_session.as_ref());", "recovery configure", 4),
    ("    platform::install_url_scheme_handler();", "exception guards + url handler", 4),
    ("    let runtime = app_runtime();", "tokio runtime", 4),
    ("    let app = gpui_platform::application().with_assets(Assets);", "gpui application", 4),
    ("        gpui_tokio::init_from_handle(cx, runtime_handle);", "closure start", 8),
    ('        platform::set_dock_icon(include_bytes!("../assets/dock-icon.png"));', "dock icon", 8),
    ("        crate::meetings_ui::documents::bind_keys(cx);", "fonts + key bindings", 8),
    ("        let general_settings = crate::store::GeneralSettings::load();", "settings load", 8),
    ("        crate::i18n::init(general_settings.ui_language.as_deref());", "i18n", 8),
    ("        crate::theme::init(general_settings.theme, cx);", "theme", 8),
    ("        keylat::install(cx);", "reduce motion + keylat", 8),
    ("        let session = RecordingSession::init(cx);", "RecordingSession::init", 8),
    ("        crate::sign_in::SignIn::init(cx);", "SignIn::init", 8),
    ("        crate::feeds::Feeds::init(cx);", "Feeds::init", 8),
    ("        crate::media_access::MediaAccess::init(cx);", "MediaAccess::init", 8),
    ("        crate::meetings::Meetings::init(cx);", "Meetings::init", 8),
    ("        crate::meetings_ui::init(cx);", "meetings_ui::init", 8),
    ("        crate::target_overlay::TargetSelect::init(cx);", "TargetSelect::init", 8),
    ("        startup::finished();", "open_window", 8),
    ("        app_windows::init(window_handle, session, cx);", "app_windows::init", 8),
    ("        power::install(cx);", "power::install", 8),
    ("        updates::schedule_startup_check(cx);", "quit handler + updates", 8),
    ("        app_windows::schedule_launch_render_warmup(cx);", "render warmup scheduled", 8),
    ("        upload::queue::init(cx);", "upload queue", 8),
    ("        upload::instant_cleanup::init(cx);", "instant cleanup", 8),
    ("        automation::init(cx);", "automation", 8),
    ("        hotkeys::init(cx);", "hotkeys", 8),
    ("        tray::drive_from_env(cx);", "onboarding gate + tray env", 8),
    ("        menus::sync_dock_visibility(cx);", "dock visibility", 8),
    ('            .expect("failed to start device enumeration");', "main window update", 8),
]
for anchor, name, indent in steps:
    main = insert_after(main, anchor, probe(name, indent))

main = insert_after(
    main,
    "        if !std::env::var(\"CAP_GPUI_NO_MENUS\").is_ok_and(|v| v == \"1\") {\n            menus::init(cx);\n        }",
    probe("menus::init", 8),
)
main = insert_after(
    main,
    "        if !std::env::var(\"CAP_GPUI_NO_TRAY\").is_ok_and(|v| v == \"1\") {\n            tray::init(cx);\n        }",
    probe("tray::init", 8),
)
main = insert_after(
    main,
    "        if !dev_restore::enabled() {\n            cx.activate(true);\n        }",
    probe("closure end", 8),
)
(root / "src/main.rs").write_text(main)

window = (root / "src/main_window.rs").read_text()
window = insert_after(
    window,
    "impl Render for MainWindow {\n    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {",
    """
        {
            static FIRST: std::sync::Once = std::sync::Once::new();
            FIRST.call_once(|| {
                tracing::info!("probe: first render start");
                cx.defer(|_| tracing::info!("probe: first frame submitted"));
                window.on_next_frame(|_, _| tracing::info!("probe: second frame start"));
            });
        }""",
)
(root / "src/main_window.rs").write_text(window)
print("probe lines added")
