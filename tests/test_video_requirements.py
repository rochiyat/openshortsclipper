"""Campaign requirements: parsed strictly, enforced by code where code can."""
import asyncio
import base64
import io
import json
import os
import shutil
import subprocess

import httpx
import pytest
from PIL import Image

import video_requirements as vr
import watermarked


def logo_data_url(size=(40, 20), fmt="PNG", color=(255, 0, 0, 255)):
    buf = io.BytesIO()
    img = Image.new("RGBA", size, color)
    (img if fmt == "PNG" else img.convert("RGB")).save(buf, format=fmt)
    mime = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp"}[fmt]
    return f"data:image/{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


class TestParse:
    @pytest.mark.parametrize("empty", [None, "", {}, {"link": "  ", "must_avoid": ""}])
    def test_empty_means_no_requirements(self, empty):
        assert vr.parse(empty) == ({}, None)

    def test_accepts_the_json_string_the_upload_form_sends(self):
        spec, logo = vr.parse(json.dumps({"link": "example.com/x", "link_on_video": True}))
        assert spec["link"] == "example.com/x" and spec["link_on_video"] is True
        assert logo is None

    def test_link_on_video_needs_a_link(self):
        spec, _ = vr.parse({"overlay_text": "ad", "link_on_video": True})
        assert spec["link_on_video"] is False

    @pytest.mark.parametrize("bad", [
        "{not json", "[]", {"link": "two words"}, {"link": "x" * 201},
        {"overlay_text": "x" * 121}, {"must_avoid": "x" * 1001}, {"must_include": 5},
        {"logo": "https://example.com/logo.png"}, {"logo": "data:image/svg+xml;base64,AAAA"},
        {"logo": "data:image/png;base64,bm90IGFuIGltYWdl"},
        {"logo": logo_data_url(), "logo_position": "center"},
        {"logo": logo_data_url(), "logo_width": 0.9},
    ])
    def test_bad_input_is_a_value_error(self, bad):
        with pytest.raises(ValueError):
            vr.parse(bad)

    def test_oversized_logo_is_refused(self):
        payload = base64.b64encode(b"x" * (vr.MAX_LOGO_BYTES + 1)).decode()
        with pytest.raises(ValueError, match="limited"):
            vr.parse({"logo": f"data:image/png;base64,{payload}"})

    @pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
    def test_logo_is_normalised_to_png(self, fmt):
        spec, logo = vr.parse({"logo": logo_data_url(fmt=fmt)})
        with Image.open(io.BytesIO(logo)) as img:
            assert img.format == "PNG" and img.mode == "RGBA"
        assert spec["logo_position"] == vr.DEFAULT_LOGO_POSITION
        assert "logo" not in spec

    def test_control_characters_are_removed(self):
        spec, _ = vr.parse({"overlay_text": "a\x07b\nc"})
        assert spec["overlay_text"] == "a b c"


class TestStorage:
    def test_round_trip(self, tmp_path):
        spec, logo = vr.parse({"logo": logo_data_url(), "must_avoid": "rokok"})
        vr.save(str(tmp_path), spec, logo)
        assert vr.load(str(tmp_path)) == spec
        assert (tmp_path / vr.LOGO_FILE).exists()

    def test_a_job_without_a_spec_loads_empty(self, tmp_path):
        assert vr.load(str(tmp_path)) == {}
        assert vr.load(None) == {}
        vr.save(str(tmp_path), {})
        assert not (tmp_path / vr.SPEC_FILE).exists()

    def test_the_spec_files_are_not_servable(self):
        import media_auth
        assert not media_auth.is_servable(f"job/{vr.SPEC_FILE}")
        assert not media_auth.is_servable(f"job/{vr.LOGO_FILE}")


class TestPromptBlock:
    def test_no_rules_leave_the_prompt_untouched(self):
        assert vr.prompt_block({}) == ""
        assert vr.prompt_block({"link": "example.com"}) == ""

    def test_rules_reach_the_prompt(self):
        block = vr.prompt_block({"must_include": "sebut produk", "must_avoid": "rokok"})
        assert "sebut produk" in block and "rokok" in block

    def test_only_the_rule_given_is_stated(self):
        block = vr.prompt_block({"must_avoid": "rokok"})
        assert "MUST AVOID" in block.split("How to apply")[0]
        assert "MUST INCLUDE" not in block.split("How to apply")[0]

    def test_braces_in_a_rule_are_safe(self):
        # Appended after str.format, so a stray brace cannot break a template.
        assert "{x}" in vr.prompt_block({"must_avoid": "{x}"})

    def test_activate_sets_the_default(self):
        vr.activate({"must_avoid": "rokok"})
        try:
            assert "rokok" in vr.prompt_block()
        finally:
            vr.activate({})
        assert vr.prompt_block() == ""


class TestDescriptions:
    def test_link_and_text_reach_both_descriptions_once(self):
        clip = {"video_description_for_tiktok": "a", "video_description_for_instagram": "b",
                "video_title_for_youtube_short": "t"}
        spec = {"link": "example.com", "description_text": "#ad"}
        vr.apply_to_descriptions(clip, spec)
        vr.apply_to_descriptions(clip, spec)
        assert clip["video_description_for_tiktok"] == "a\n\nexample.com\n\n#ad"
        assert clip["video_description_for_instagram"] == "b\n\nexample.com\n\n#ad"
        assert clip["video_title_for_youtube_short"] == "t"

    def test_nothing_required_changes_nothing(self):
        clip = {"video_description_for_tiktok": "a"}
        vr.apply_to_descriptions(clip, {"must_avoid": "x"})
        assert clip == {"video_description_for_tiktok": "a"}


class TestNames:
    @pytest.mark.parametrize("name,expected", [
        ("t_clip_1.mp4", False), ("brand_t_clip_1.mp4", True),
        ("wm_brand_subtitled_1_t_clip_1.mp4", True),
        ("translated_es_brand_t_clip_1.mp4", True),
        # A video that merely has the word in its title is not branded.
        ("my_brand_video_clip_1.mp4", False),
        ("subtitled_1_brand_new_clip_1.mp4", False),
    ])
    def test_carries_brand(self, name, expected):
        assert vr.carries_brand(name) is expected

    def test_a_brand_copy_on_a_free_job_is_a_clean_deliverable(self):
        assert watermarked.is_clean_deliverable("brand_subtitled_1_t_clip_1.mp4")
        assert watermarked.clean_name("wm_brand_t_clip_1.mp4") == "brand_t_clip_1.mp4"


class TestOverlay:
    def test_needs_overlay_only_for_what_is_burned(self, tmp_path):
        d = str(tmp_path)
        assert not vr.needs_overlay(d, {})
        assert not vr.needs_overlay(d, {"link": "x", "link_on_video": False, "must_avoid": "y"})
        assert vr.needs_overlay(d, {"link": "x", "link_on_video": True})
        assert vr.needs_overlay(d, {"overlay_text": "ad"})
        (tmp_path / vr.LOGO_FILE).write_bytes(b"x")
        assert vr.needs_overlay(d, {"must_avoid": "y"})

    @pytest.mark.parametrize("position,side", [("top-left", "left"), ("top-right", "right"),
                                               ("top-center", "center")])
    def test_logo_lands_where_asked_inside_the_top_margin(self, tmp_path, position, side):
        spec, logo = vr.parse({"logo": logo_data_url((100, 100)), "logo_position": position})
        vr.save(str(tmp_path), spec, logo)
        frame = vr.build_overlay(str(tmp_path), spec, 1080, 1920)
        assert frame.size == (1080, 1920)
        left, top, right, _ = frame.getbbox()
        assert top == int(1920 * 0.06)
        if side == "left":
            assert left == int(1080 * 0.05)
        elif side == "right":
            assert right == 1080 - int(1080 * 0.05)
        else:
            assert abs((left + right) / 2 - 540) <= 1

    def test_text_stays_in_frame_and_above_the_hook(self, tmp_path):
        spec, _ = vr.parse({"link": "example.com/" + "x" * 100, "link_on_video": True})
        frame = vr.build_overlay(str(tmp_path), spec, 1080, 1920)
        left, top, right, bottom = frame.getbbox()
        assert 0 < left and right < 1080 and top >= int(1920 * 0.06)
        assert bottom < 1920 * 0.25

    def test_cta_is_optional(self):
        assert vr.parse({"cta_text": "   "}) == ({}, None)
        assert vr.parse({"cta_text": "Follow for part 2"})[0]["cta_text"] == "Follow for part 2"
        with pytest.raises(ValueError):
            vr.parse({"cta_text": "x" * 121})

    def test_cta_needs_an_overlay_only_when_filled(self, tmp_path):
        assert vr.needs_overlay(str(tmp_path), {"cta_text": "Follow"})
        assert not vr.needs_overlay(str(tmp_path), {"cta_text": "", "must_avoid": "y"})

    def test_cta_card_stays_clear_of_the_hook_seam_and_the_bottom(self):
        frame = vr.build_cta_overlay("x " * 60, 1080, 1920)
        left, top, right, bottom = frame.getbbox()
        assert 0 < left and right < 1080
        assert top > 1920 * 0.52 and bottom < 1920 * 0.82

    def test_cta_text_never_reaches_the_filtergraph(self):
        assert vr.overlay_filter() == "[0:v][1:v]overlay=0:0:format=auto"
        assert "enable='gte(t,6.500)'" in vr.overlay_filter(6.5)

    @pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
    def test_cta_shows_only_at_the_end(self, tmp_path):
        vr.save(str(tmp_path), {"cta_text": "Follow"})
        final = tmp_path / "t_clip_1.mp4"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=360x640:d=6",
                        "-pix_fmt", "yuv420p", str(final)], check=True, capture_output=True)
        out = vr.brand_delivery(str(final))
        assert out == str(tmp_path / "brand_t_clip_1.mp4")
        assert not list(tmp_path.glob(".overlay_*"))

        def brightest_at(seconds):
            raw = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", str(seconds), "-i", out, "-frames:v", "1",
                 "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True).stdout
            return max(raw)

        assert brightest_at(1) < 60
        assert brightest_at(5) > 200

    def test_no_overlay_means_the_same_file(self, tmp_path):
        final = tmp_path / "t_clip_1.mp4"
        final.write_bytes(b"clip")
        assert vr.brand_delivery(str(final)) == str(final)

    def test_a_failed_burn_keeps_the_clip(self, tmp_path):
        vr.save(str(tmp_path), {"overlay_text": "ad"})
        final = tmp_path / "t_clip_1.mp4"
        final.write_bytes(b"not a video")
        assert vr.brand_delivery(str(final)) == str(final)
        assert sorted(p.name for p in tmp_path.iterdir()) == [vr.SPEC_FILE, "t_clip_1.mp4"]

    @pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
    def test_burns_a_copy_and_reuses_it(self, tmp_path):
        spec, logo = vr.parse({"logo": logo_data_url((64, 64)), "overlay_text": "iklan"})
        vr.save(str(tmp_path), spec, logo)
        final = tmp_path / "t_clip_1.mp4"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=360x640:d=0.5",
                        "-pix_fmt", "yuv420p", str(final)], check=True, capture_output=True)
        before = final.read_bytes()
        out = vr.brand_delivery(str(final))
        assert out == str(tmp_path / "brand_t_clip_1.mp4")
        assert final.read_bytes() == before
        stamp = (tmp_path / "brand_t_clip_1.mp4").stat().st_mtime_ns
        assert vr.brand_delivery(str(final)) == out
        assert (tmp_path / "brand_t_clip_1.mp4").stat().st_mtime_ns == stamp
        # Not stacked a second time when an edit hands back the branded file.
        assert vr.brand_delivery(out) == out
        assert not list(tmp_path.glob(".overlay_*"))


    @pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
    def test_a_refused_gpu_encoder_falls_back_to_x264(self, tmp_path, monkeypatch):
        import ffmpeg_utils
        monkeypatch.setattr(ffmpeg_utils, "video_encode_args",
                            lambda tier=None: ["-c:v", "no_such_encoder"])
        vr.save(str(tmp_path), {"overlay_text": "iklan"})
        final = tmp_path / "t_clip_1.mp4"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=360x640:d=0.5",
                        "-pix_fmt", "yuv420p", str(final)], check=True, capture_output=True)
        assert vr.brand_delivery(str(final)) == str(tmp_path / "brand_t_clip_1.mp4")


class TestProcessEndpoint:
    """Both ways /api/process receives the field: a JSON body and an upload's form."""

    @pytest.fixture()
    def app_module(self, tmp_path, monkeypatch):
        app = pytest.importorskip("app")
        for attr, name in (("OUTPUT_DIR", "output"), ("UPLOAD_DIR", "uploads")):
            (tmp_path / name).mkdir()
            monkeypatch.setattr(app, attr, str(tmp_path / name))
        monkeypatch.setattr(app, "_media_duration_seconds", lambda p: 120.0)

        async def fake_put(item):
            pass
        monkeypatch.setattr(app.job_queue, "put", fake_put)
        return app

    @staticmethod
    def _post(app, **kwargs):
        async def _do():
            transport = httpx.ASGITransport(app=app.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                         headers={"X-Gemini-Key": "test-key"}) as c:
                return await c.post("/api/process", **kwargs)
        return asyncio.run(_do())

    def _upload(self, app, requirements):
        return self._post(app, files={"file": ("v.mp4", b"x" * 100, "video/mp4")},
                          data={"acknowledged": "true", "video_requirements": requirements})

    def test_an_upload_saves_the_spec_and_the_logo_in_the_job_dir(self, app_module):
        resp = self._upload(app_module, json.dumps(
            {"logo": logo_data_url(), "link": "example.com", "must_avoid": "rokok"}))
        assert resp.status_code == 200, resp.text
        job_dir = app_module.jobs[resp.json()["job_id"]]["output_dir"]
        assert vr.load(job_dir)["must_avoid"] == "rokok"
        assert os.path.exists(os.path.join(job_dir, vr.LOGO_FILE))

    def test_bad_input_is_a_400_on_an_upload(self, app_module):
        resp = self._upload(app_module, json.dumps({"link": "two words"}))
        assert resp.status_code == 400 and "video_requirements" in resp.text

    def test_bad_input_is_a_400_on_a_json_body(self, app_module):
        resp = self._post(app_module, json={"url": "https://example.com/v", "acknowledged": True,
                                            "video_requirements": {"logo_position": "x",
                                                                   "logo": logo_data_url()}})
        assert resp.status_code == 400 and "video_requirements" in resp.text

    def test_a_job_without_the_field_writes_nothing(self, app_module):
        resp = self._post(app_module, files={"file": ("v.mp4", b"x" * 100, "video/mp4")},
                          data={"acknowledged": "true"})
        assert resp.status_code == 200, resp.text
        job_dir = app_module.jobs[resp.json()["job_id"]]["output_dir"]
        assert not os.path.exists(os.path.join(job_dir, vr.SPEC_FILE))


class TestWalkBacks:
    def test_edits_start_from_the_file_under_the_overlay(self, tmp_path):
        app = pytest.importorskip("app")
        for name in ("t_clip_1.mp4", "subtitled_1_t_clip_1.mp4",
                     "brand_subtitled_1_t_clip_1.mp4", "wm_brand_subtitled_1_t_clip_1.mp4"):
            (tmp_path / name).write_bytes(b"x")
        d = str(tmp_path)
        assert app._strip_watermark_copy(d, "wm_brand_subtitled_1_t_clip_1.mp4") == \
            "subtitled_1_t_clip_1.mp4"
        assert app._strip_burned_captions(d, "brand_subtitled_1_t_clip_1.mp4") == "t_clip_1.mp4"

    def test_a_missing_twin_leaves_the_name(self, tmp_path):
        app = pytest.importorskip("app")
        (tmp_path / "brand_t_clip_1.mp4").write_bytes(b"x")
        assert app._strip_watermark_copy(str(tmp_path), "brand_t_clip_1.mp4") == "brand_t_clip_1.mp4"

    def test_the_branded_file_is_the_one_a_restore_finds(self, tmp_path):
        app = pytest.importorskip("app")
        (tmp_path / "t_clip_1.mp4").write_bytes(b"x")
        (tmp_path / "brand_t_clip_1.mp4").write_bytes(b"x")
        assert app._canonical_clip_file(str(tmp_path), "t", 0) == "brand_t_clip_1.mp4"
