"""
Unit tests for gamebot.video module.
Validates ShortsGenerator, Pillow transparent badge creation, dimensions (720x1280),
and FFmpeg 9:16 vertical video conversion filter complex.
"""

import os
import tempfile
import subprocess
from unittest.mock import patch, MagicMock
from PIL import Image

from gamebot.video import ShortsGenerator


def test_shorts_generator_init():
    gen_default = ShortsGenerator()
    assert gen_default.raw_ffmpeg_path == "ffmpeg"

    gen_custom = ShortsGenerator("/custom/path/ffmpeg")
    assert gen_custom.raw_ffmpeg_path == "/custom/path/ffmpeg"


def test_is_ffmpeg_available_success():
    gen = ShortsGenerator()
    mock_res = MagicMock()
    mock_res.returncode = 0

    with patch("subprocess.run", return_value=mock_res) as mock_run:
        assert gen.is_ffmpeg_available() is True
        mock_run.assert_called_once()


def test_is_ffmpeg_available_failure():
    gen = ShortsGenerator()

    # Case 1: non-zero return code
    mock_res = MagicMock()
    mock_res.returncode = 1
    with patch("subprocess.run", return_value=mock_res):
        assert gen.is_ffmpeg_available() is False

    # Case 2: FileNotFoundError / exception
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert gen.is_ffmpeg_available() is False


def test_create_overlay_badge_real_pillow_rendering():
    gen = ShortsGenerator()

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        badge_path = tmp.name

    try:
        out = gen.create_overlay_badge(
            title="The Witcher 3: Wild Hunt - Complete Edition",
            watermark="@gameworld2029",
            output_png=badge_path,
        )
        assert out == badge_path
        assert os.path.exists(badge_path)

        # Inspect resulting image
        with Image.open(badge_path) as img:
            assert img.size == (720, 1280)
            assert img.mode == "RGBA"

            # Check that corners or canvas has transparency
            corner_pixel = img.getpixel((0, 0))
            assert corner_pixel[3] == 0  # Fully transparent background

            # Check that badge area (e.g. center of title badge ~ 360, 150) is non-transparent
            center_badge_pixel = img.getpixel((360, 150))
            assert center_badge_pixel[3] > 0  # Solid/semi-transparent badge drawn

            # Check that watermark badge area (360, 1190) is non-transparent
            wm_pixel = img.getpixel((360, 1190))
            assert wm_pixel[3] > 0

    finally:
        if os.path.exists(badge_path):
            os.unlink(badge_path)


def test_create_overlay_badge_auto_temporary_file():
    gen = ShortsGenerator()
    created_path = gen.create_overlay_badge(
        title="Cyberpunk 2077",
        watermark="@gameworld2029",
        output_png=None,
    )
    try:
        assert os.path.exists(created_path)
        with Image.open(created_path) as img:
            assert img.size == (720, 1280)
    finally:
        if os.path.exists(created_path):
            os.unlink(created_path)


def test_build_shorts_missing_input_file():
    gen = ShortsGenerator()
    result = gen.build_shorts(
        input_video_path="non_existent_video.mp4",
        title="Test Game",
        output_video_path="output.mp4",
    )
    assert result is False


def test_build_shorts_ffmpeg_unavailable():
    gen = ShortsGenerator()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
        in_path = tmp_in.name

    try:
        with patch.object(gen, "is_ffmpeg_available", return_value=False):
            result = gen.build_shorts(
                input_video_path=in_path,
                title="Test Game",
                output_video_path="out.mp4",
            )
            assert result is False
    finally:
        if os.path.exists(in_path):
            os.unlink(in_path)


def test_build_shorts_command_construction_and_cleanup():
    gen = ShortsGenerator("ffmpeg")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
        in_path = tmp_in.name
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_out:
        out_path = tmp_out.name
        # Pre-populate output file so exists check passes
        tmp_out.write(b"encoded_video_data")

    try:
        with patch.object(gen, "is_ffmpeg_available", return_value=True):
            mock_run_res = MagicMock()
            mock_run_res.returncode = 0
            mock_run_res.stderr = ""

            captured_cmd = []

            def fake_subprocess_run(cmd, *args, **kwargs):
                nonlocal captured_cmd
                captured_cmd = cmd
                # Ensure output file has non-zero size
                with open(out_path, "wb") as f:
                    f.write(b"output_mp4_bytes")
                return mock_run_res

            with patch("subprocess.run", side_effect=fake_subprocess_run):
                success = gen.build_shorts(
                    input_video_path=in_path,
                    title="Half-Life 2: Episode Two",
                    output_video_path=out_path,
                    max_duration=40,
                    watermark="@gameworld2029",
                )

                assert success is True

                # Inspect captured command parameters
                cmd_str = " ".join(captured_cmd)
                assert "-i " + in_path in cmd_str
                assert "-filter_complex" in captured_cmd
                filter_index = captured_cmd.index("-filter_complex")
                fc = captured_cmd[filter_index + 1]

                # Check filter complex details
                assert "split=2[bg_in][fg_in]" in fc
                assert "scale=720:1280" in fc
                assert "boxblur=22:22" in fc
                assert "scale=720:-2" in fc
                assert "overlay=(W-w)/2:(H-h)/2" in fc
                assert "overlay=0:0" in fc

                # Check encoding flags
                assert "-c:v" in captured_cmd
                assert "libx264" in captured_cmd
                assert "-preset" in captured_cmd
                assert "fast" in captured_cmd
                assert "-crf" in captured_cmd
                assert "22" in captured_cmd
                assert "-pix_fmt" in captured_cmd
                assert "yuv420p" in captured_cmd
                assert "-c:a" in captured_cmd
                assert "aac" in captured_cmd
                assert "-t" in captured_cmd
                assert "40" in captured_cmd
                assert out_path in captured_cmd

    finally:
        for p in (in_path, out_path):
            if os.path.exists(p):
                os.unlink(p)


def test_build_shorts_handles_ffmpeg_error():
    gen = ShortsGenerator("ffmpeg")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
        in_path = tmp_in.name

    try:
        with patch.object(gen, "is_ffmpeg_available", return_value=True):
            mock_run_res = MagicMock()
            mock_run_res.returncode = 1
            mock_run_res.stderr = "Codec configuration error"

            with patch("subprocess.run", return_value=mock_run_res):
                success = gen.build_shorts(
                    input_video_path=in_path,
                    title="Error Game",
                    output_video_path="output.mp4",
                )
                assert success is False
    finally:
        if os.path.exists(in_path):
            os.unlink(in_path)
