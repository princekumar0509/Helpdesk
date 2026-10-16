"""Assemble the presentation video from the recorded screen clips, the slides and the narration.

    python make_video.py                       # silent preview, each part at its target length
    python make_video.py --audio audio/        # final video, cut to the narration in audio/partNN.m4a

Inputs (git-ignored):
    recording/*.webm                 screen recordings of each demo part (1920x1080)
    recording/slides/slide-NN.png    slides rendered from HelpDesk-Presentation.pdf at 192 dpi
    audio/partNN.<m4a|mp3|wav>       narration, one file per part (part02b/part08b are optional)
    audio/transcript.json            word timestamps of the narration (faster-whisper)

Each part is a list of *beats*. A beat starts when the narrator says its cue phrase (looked up in
the transcript) and lasts until the next beat's cue, so the picture changes when the voice does.
Inside its slot a beat's footage plays at natural speed, is sped up evenly if it is too long
(capped), and holds its last frame if it is too short. Output: recording/HelpDesk-Presentation.mp4
"""
import argparse, json, re, subprocess, sys, tempfile
from pathlib import Path

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FFMPEG = "ffmpeg"

HERE = Path(__file__).resolve().parent
REC = HERE / "recording"
SLIDES = REC / "slides"

def slide(n, secs=8):
    return {"img": SLIDES / f"slide-{n:02d}.png", "dur": secs}

def clip(name, start=0.0, end=None, speed=1.0):
    return {"vid": REC / name, "start": start, "end": end, "speed": speed}

def beat(cue, *pieces):
    """cue: phrase the narrator says when this beat starts (None = start of the part)."""
    return {"cue": cue, "pieces": list(pieces)}

# (key, title, preview length, optional, beats). Order follows SUBMISSION.md: slides and
# architecture, codebase, the demo modules, CI/CD, Terraform, Argo CD, Kubernetes, monitoring.
PARTS = [
    ("part01", "Introduction", 45, False, [
        beat(None, slide(1)),
        beat("support ticketing system", slide(2)),
        beat("In this presentation", slide(4))]),
    ("part02", "Architecture", 60, False, [beat(None, slide(3))]),
    ("part02b", "Codebase walkthrough", 63, True, [
        beat(None, clip("p02b-codebase.webm", 1.0, 15.0)),
        beat("backend is the fast", clip("p02b-codebase.webm", 15.0, 31.0)),
        beat("Alembic folder", clip("p02b-codebase.webm", 31.0, 34.0)),
        beat("the test folder", clip("p02b-codebase.webm", 34.0, 38.0)),
        beat("Frontend is the react", clip("p02b-codebase.webm", 38.0, 43.0)),
        beat("runs the whole stack", clip("p02b-codebase.webm", 43.0, 47.0)),
        beat("terraform creates", clip("p02b-codebase.webm", 47.0, 52.0)),
        beat("Helm helpdesk is", clip("p02b-codebase.webm", 52.0, 57.0)),
        beat("Argo CD folder", clip("p02b-codebase.webm", 57.0, 66.0)),
        beat("monitoring holds", clip("p02b-codebase.webm", 66.0, 69.0)),
        beat("GitHub workflow", clip("p02b-codebase.webm", 69.0))]),
    ("part03", "The application", 78, False, [
        beat(None, clip("p03-app.webm", 0.5, 7.4)),
        beat("Let me create a new ticket", clip("p03-app.webm", 7.4, 24.5)),
        beat("The new tickets open on the right", clip("p03-app.webm", 24.5, 43.5)),
        beat("I can also add an internal note", clip("p03-app.webm", 43.5, 51.0)),
        beat("Finally I mark it as resolved", clip("p03-app.webm", 51.0, 61.0)),
        beat("The backend is REST API", clip("p03-app.webm", 61.0))]),
    ("part03b", "The database", 25, True, [beat(None, clip("p03b-database.webm", 0.5))]),
    ("part04", "Testing", 47, False, [
        beat(None, slide(6)),
        beat("Ruff is a fast Python linter", clip("p04-tests.webm", 0.5))]),
    ("part05", "Docker", 49, False, [
        beat(None, slide(7)),
        beat("The backend Docker file has two stages", clip("p05-docker.webm", 0.5))]),
    ("part06", "CI/CD live change", 74, False, [
        beat(None, clip("p06a-commit.webm", 0.5)),
        beat("starts the github action workflow", clip("p06b-pipeline.webm", 1.0, None, 3.0)),
        beat("All three jobs are green", slide(8))]),
    ("part07", "Trivy and GHCR", 33, False, [
        beat(None, slide(9)),
        beat("So there is less software", clip("p07-ghcr.webm", 0.5, 20.0))]),
    ("part08", "Terraform", 72, False, [
        beat(None, clip("p08a-terraform.webm", 0.5)),
        beat("Here in the AWS console", clip("p08b-console.webm", 36.0))]),
    ("part08b", "GitOps with Argo CD", 60, True, [
        beat(None, slide(12)),
        beat("Here the helpdesk application", clip("p08c-argocd.webm", 14.0, 30.0)),
        beat("Self-heal is on", clip("p08c-argocd.webm", 30.0, 55.0))]),
    ("part09", "Kubernetes and Helm", 56, False, [
        beat(None, clip("p09a-kubectl.webm", 0.5)),
        beat("Now I refresh the browser", clip("p09b-live-change.webm", 9.0))]),
    ("part10", "Autoscaling and monitoring", 61, False, [
        beat(None, clip("p10a-hpa.webm", 0.5)),
        beat("For monitoring", clip("p10b-monitoring.webm", 0.5, 7.5)),
        beat("finds it automatically", clip("p10b-monitoring.webm", 13.0, 18.0)),
        beat("Grafana shows", clip("p10b-monitoring.webm", 31.0))]),
    ("part10b", "Troubleshooting", 45, True, [
        beat(None, clip("p10c-troubleshooting.webm", 0.5, 27.0)),
        beat("I fix it", clip("p10c-troubleshooting.webm", 27.0))]),
    ("part11", "Challenges", 63, False, [beat(None, slide(14))]),
    ("part12", "Cleanup and thank you", 16, False, [beat(None, slide(15))]),
]

def run(args):
    r = subprocess.run([FFMPEG, "-v", "error", "-y", *map(str, args)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"ffmpeg failed: {' '.join(map(str, args))[:300]}\n{r.stderr[-1500:]}")

def duration(path):
    r = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        sys.exit(f"cannot read duration of {path}")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])

NORM = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0F3B3A,setsar=1,fps=30,format=yuv420p"
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]

def render_piece(p, out):
    if "img" in p:
        run(["-loop", "1", "-t", p["dur"], "-i", p["img"], "-vf", NORM, *ENC, out])
    else:
        trim = ["-ss", p["start"]] + (["-to", p["end"]] if p["end"] else [])
        run([*trim, "-i", p["vid"], "-an", "-vf", f"setpts=PTS/{p['speed']}," + NORM, *ENC, out])

def concat(files, out, tmp):
    lst = Path(tmp) / f"{Path(out).stem}.txt"
    lst.write_text("".join(f"file '{f}'\n" for f in files))
    run(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])

def fit(src, slot, out, max_speed):
    """Fit footage to exactly `slot` seconds: speed up (capped) if long, hold the last frame if short."""
    have = duration(src)
    speed = min(max(have / slot, 1.0), max_speed)
    # setpts only when speeding up: a no-op setpts in front of tpad stops the last-frame hold from working
    vf = (f"setpts=PTS/{speed:.4f}," if speed > 1.0 else "") + \
         f"tpad=stop_mode=clone:stop_duration={max(slot - have / speed, 0) + 0.5:.2f},trim=duration={slot:.3f},fps=30,format=yuv420p"
    run(["-i", src, "-vf", vf, *ENC, "-fps_mode", "cfr", "-an", out])
    return have, speed

def norm(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())

def cue_time(words, phrase, after):
    """Start time of the first occurrence of `phrase` in the transcript words at or after `after`."""
    target = [norm(w) for w in phrase.split()]
    seq = [(s, norm(w)) for s, _, w in words]
    for i in range(len(seq) - len(target) + 1):
        if seq[i][0] >= after and all(seq[i + k][1] == t for k, t in enumerate(target)):
            return seq[i][0]
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", help="folder with partNN audio files and transcript.json")
    ap.add_argument("--max-speed", type=float, default=2.5)
    a = ap.parse_args()
    transcript = {}
    if a.audio and (Path(a.audio) / "transcript.json").exists():
        transcript = json.loads((Path(a.audio) / "transcript.json").read_text())
    final = REC / ("HelpDesk-Presentation.mp4" if a.audio else "HelpDesk-Presentation-preview.mp4")
    import os
    keep = os.environ.get("KEEP_TMP")
    if keep: Path(keep).mkdir(parents=True, exist_ok=True)
    with (tempfile.TemporaryDirectory() if not keep else __import__("contextlib").nullcontext(keep)) as tmp:
        parts_out = []
        for key, title, target, optional, beats in PARTS:
            audio = None
            if a.audio:
                cands = [c for c in sorted(Path(a.audio).glob(f"{key}.*")) if c.suffix != ".json"]
                if not cands:
                    if optional:
                        print(f"{key:8} {title:<28} skipped (no narration yet)")
                        continue
                    sys.exit(f"missing narration: {a.audio}/{key}.<m4a|mp3|wav>")
                audio = cands[0]
            total = duration(audio) + 0.6 if audio else float(target)
            # beat start times from the transcript; beats without a usable cue split the time evenly
            words = transcript.get(key, {}).get("words", [])
            starts, prev = [], 0.0
            for i, b in enumerate(beats):
                t = 0.0 if i == 0 else (cue_time(words, b["cue"], prev) if words else None)
                if t is None:
                    t = total * i / len(beats)
                    if words:
                        print(f"   cue not found in {key}: '{b['cue']}' (using {t:.1f}s)")
                starts.append(max(t - 0.3, prev) if i else 0.0)
                prev = starts[-1]
            ends = starts[1:] + [total]
            beat_files, info = [], []
            for i, (b, s, e) in enumerate(zip(beats, starts, ends)):
                pieces = []
                for j, p in enumerate(b["pieces"]):
                    f = Path(tmp) / f"{key}_{i}_{j}.mp4"
                    render_piece(p, f)
                    pieces.append(f)
                joined = Path(tmp) / f"{key}_{i}_joined.mp4"
                concat(pieces, joined, tmp)
                out = Path(tmp) / f"{key}_{i}_fit.mp4"
                have, speed = fit(joined, max(e - s, 0.5), out, a.max_speed)
                beat_files.append(out)
                info.append(f"{s:5.1f}s x{speed:.2f}")
            video = Path(tmp) / f"{key}_video.mp4"
            concat(beat_files, video, tmp)
            out = Path(tmp) / f"{key}.mp4"
            if audio:
                asrc = ["-i", audio]
                af = f"[1:a]aresample=48000,apad=whole_dur={total:.2f}[a]"
            else:
                asrc = ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=48000:cl=stereo"]
                af = "[1:a]anull[a]"
            # pin the picture to exactly the narration length so parts never drift apart when joined
            vf = f"[0:v]tpad=stop_mode=clone:stop_duration=3,trim=duration={total:.3f},fps=30,format=yuv420p[v]"
            run(["-i", video, *asrc, "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]", *ENC, "-fps_mode", "cfr",
                 "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-t", f"{total:.2f}", out])
            parts_out.append(out)
            print(f"{key:8} {title:<28} {total:6.1f}s  beats: {' | '.join(info)}", flush=True)
        concat(parts_out, final, tmp)
    print(f"\n{final}  ({duration(final) / 60:.1f} min)")

if __name__ == "__main__":
    main()
