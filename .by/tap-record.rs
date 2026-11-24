// tap-record <seconds> <out prefix>: an independent witness of the computer's output mix. Writes the
// tap's real chunks as f32 mono to <prefix>.f32 and one line per chunk to <prefix>.idx
// ("t_ms offset frames synthetic"), so a glitch in what another app plays (a gap or a click across
// someone else's tap restart) shows in the samples. Scratch, builder BY; never landed.
#[cfg(target_os = "macos")]
fn main() {
    use std::{
        fs::File,
        io::{BufWriter, Write},
        sync::{Arc, Mutex},
        time::{Duration, Instant},
    };
    let mut args = std::env::args().skip(1);
    let seconds: u64 = args.next().and_then(|v| v.parse().ok()).unwrap_or(60);
    let prefix = args.next().unwrap_or_else(|| "witness".into());
    let data = Arc::new(Mutex::new(BufWriter::new(File::create(format!("{prefix}.f32")).unwrap())));
    let idx = Arc::new(Mutex::new(BufWriter::new(File::create(format!("{prefix}.idx")).unwrap())));
    let t0 = Instant::now();
    let tap = cap_meeting_platform::SystemAudioTap::start(false, {
        let data = data.clone();
        let idx = idx.clone();
        move |chunk| {
            let ms = t0.elapsed().as_millis();
            writeln!(idx.lock().unwrap(), "{ms} {} {} {} {}", chunk.frame_offset, chunk.samples.len(),
                     u8::from(chunk.synthetic), chunk.sample_rate).ok();
            if !chunk.synthetic {
                let mut d = data.lock().unwrap();
                for s in chunk.samples {
                    d.write_all(&s.to_le_bytes()).ok();
                }
            }
        }
    })
    .expect("tap");
    println!("witness started {:?}", tap.stats());
    std::thread::sleep(Duration::from_secs(seconds));
    println!("witness done {:?}", tap.stats());
    drop(tap);
    data.lock().unwrap().flush().ok();
    idx.lock().unwrap().flush().ok();
}

#[cfg(not(target_os = "macos"))]
fn main() {}
