"""Hook entrance animations on the server render (hooks.ANIMATIONS), drawn as
the dashboard preview draws them (remotion/compositions/HookOverlay.tsx)."""
import os
import shutil
import subprocess

import pytest

import hooks


@pytest.fixture
def fake_ffmpeg(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(hooks.subprocess, "check_output", lambda *a, **k: b"1080x1920\n")

    def run(cmd, **kw):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, b"", b"")
    monkeypatch.setattr(hooks.subprocess, "run", run)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    return calls, str(video)


def _graph(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def _image_args(cmd):
    """Everything between the video's input and the filter graph."""
    return cmd[cmd.index("-i") + 2:cmd.index("-filter_complex")]


class TestNormalise:
    @pytest.mark.parametrize("value,expected", [
        ("fade", "fade"), ("slide-up", "slide-up"), ("spring", "spring"),
        ("Bounce", "spring"), ("none", "none"), (None, None), ("", None),
        ("explode", None),
    ])
    def test_names(self, value, expected):
        assert hooks.normalise_animation(value) == expected


class TestGraph:
    def test_no_animation_keeps_the_still_overlay(self, fake_ffmpeg, tmp_path):
        calls, video = fake_ffmpeg
        hooks.add_hook_to_video(video, "Hi", str(tmp_path / "o.mp4"), position="top", duration=5)
        cmd = calls["cmd"]
        assert _image_args(cmd)[0] == "-i" and "-loop" not in cmd
        assert _graph(cmd).startswith("[0:v][1:v]overlay=")
        assert "fade" not in _graph(cmd)

    def test_unknown_animation_is_the_still_overlay(self, fake_ffmpeg, tmp_path):
        calls, video = fake_ffmpeg
        hooks.add_hook_to_video(video, "Hi", str(tmp_path / "o.mp4"), position="top",
                                duration=5, animation="explode")
        assert "-loop" not in calls["cmd"]

    def test_fade_loops_the_image_for_the_hook_window(self, fake_ffmpeg, tmp_path):
        calls, video = fake_ffmpeg
        hooks.add_hook_to_video(video, "Hi", str(tmp_path / "o.mp4"), position="top",
                                duration=5, animation="fade")
        cmd = calls["cmd"]
        args = _image_args(cmd)
        assert args[:4] == ["-loop", "1", "-framerate", str(hooks.ANIM_FPS)]
        assert args[4:6] == ["-t", "5"]
        g = _graph(cmd)
        assert "fade=t=in:st=0:d=0.5:alpha=1" in g
        assert "fade=t=out:st=4.5:d=0.5:alpha=1" in g
        assert ":eof_action=pass" in g and "enable='between(t,0,5.0)'" in g
        # Only one [0:v]: the GPU decode rewrite (gpu_decode.graph_ok) still applies.
        assert g.count("[0:v]") == 1

    def test_none_still_fades_out_like_the_preview(self, fake_ffmpeg, tmp_path):
        calls, video = fake_ffmpeg
        hooks.add_hook_to_video(video, "Hi", str(tmp_path / "o.mp4"), position="top",
                                duration=3, animation="none")
        g = _graph(calls["cmd"])
        assert "fade=t=in" not in g and "fade=t=out:st=2.5:d=0.5" in g

    def test_whole_clip_hook_has_no_exit_and_ends_with_the_video(self, fake_ffmpeg, tmp_path):
        calls, video = fake_ffmpeg
        hooks.add_hook_to_video(video, "Hi", str(tmp_path / "o.mp4"), position="top",
                                animation="fade")
        cmd = calls["cmd"]
        assert "-t" not in _image_args(cmd)
        g = _graph(cmd)
        assert "fade=t=out" not in g and ":shortest=1" in g and "enable" not in g

    def test_slide_up_moves_y_and_settles_on_the_spot(self, fake_ffmpeg, tmp_path):
        calls, video = fake_ffmpeg
        hooks.add_hook_to_video(video, "Hi", str(tmp_path / "o.mp4"), position="top",
                                duration=5, animation="slide-up")
        g = _graph(calls["cmd"])
        y0 = int(1920 * 0.20)
        assert f"y='{y0}+60*exp(-9.0*t)" in g

    def test_spring_scales_around_the_box_centre(self, fake_ffmpeg, tmp_path):
        calls, video = fake_ffmpeg
        hooks.add_hook_to_video(video, "Hi", str(tmp_path / "o.mp4"), position="top",
                                duration=5, animation="spring")
        g = _graph(calls["cmd"])
        assert "scale=w='if(lt(t,1)" in g and ":eval=frame" in g
        assert "-w)/2'" in g and "-h)/2'" in g

    def test_combined_caption_pass_keeps_the_logo_as_input_two(self, fake_ffmpeg, tmp_path):
        calls, video = fake_ffmpeg

        def make_filter(vw, vh, video, logo, out):
            return f"{logo}scale=324:-1[wm];{video}[wm]overlay=54:768{out}"

        hooks.add_hook_to_video(video, "Hi", str(tmp_path / "h.mp4"), position="top",
                                duration=5, animation="fade",
                                also=("ass=subs.ass", str(tmp_path / "s.mp4")),
                                mark=("logo.png", make_filter, str(tmp_path / "wm.mp4")))
        cmd = calls["cmd"]
        inputs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
        assert inputs[0] == video and inputs[2] == "logo.png"
        assert ",ass=subs.ass,split=2[s][sm];" in _graph(cmd)

    def test_recipe_replays_the_same_animation(self, fake_ffmpeg, monkeypatch, tmp_path):
        calls, video = fake_ffmpeg
        hooked = str(tmp_path / "hooked.mp4")
        hooks.add_hook_to_video(video, "Hi", hooked, position="top", duration=5,
                                animation="slide-up",
                                also=("ass=subs.ass", str(tmp_path / "s.mp4")))
        burn = calls["cmd"]

        def run(cmd, **kw):
            calls["cmd"] = cmd
            open(cmd[-1], "wb").write(b"mp4")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        monkeypatch.setattr(hooks.subprocess, "run", run)
        assert hooks.materialize(str(tmp_path), "hooked.mp4")
        cmd = calls["cmd"]
        assert _graph(cmd) == _graph(burn).split(",ass=")[0]
        assert _image_args(cmd)[:-1] == _image_args(burn)[:-1]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
@pytest.mark.parametrize("animation", ["fade", "slide-up", "spring"])
def test_real_render_fades_in_and_out(tmp_path, monkeypatch, animation):
    """A white pill on black: barely there on the first frame, fully drawn
    mid-window, gone after the window."""
    video = str(tmp_path / "clip.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=360x640:r=30:d=2.5",
                    "-pix_fmt", "yuv420p", video], check=True)
    out = str(tmp_path / "hooked.mp4")
    hooks.add_hook_to_video(video, "Hello world", out, position="center",
                            duration=1.5, animation=animation)

    def brightness(t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", out,
                              "-frames:v", "1", "-vf", "crop=24:8:168:316,format=gray",
                              "-f", "rawvideo", "-"], check=True,
                             stdout=subprocess.PIPE).stdout
        return sum(raw) / len(raw)

    first, middle, after = brightness(0), brightness(0.8), brightness(2.0)
    assert middle > 80  # white pill with black letters
    assert first < middle * 0.5
    assert after < 10
