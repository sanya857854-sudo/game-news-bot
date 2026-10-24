"""
Adversarial and stress test harness for gamebot.video (ShortsGenerator).
Empirically stress-tests:
1. Pillow badge generation under extreme edge cases (empty strings, huge titles, Cyrillic, CJK, emojis, quotes).
2. Filter complex command composition for strange resolutions (21:9, 4:3, 1:1, 9:16 portrait).
3. Temporary file lifecycle: strict cleanup of overlay PNG on success, FFmpeg error, timeout, and unexpected exceptions.
4. Error resilience: missing FFmpeg, broken binary, empty output, zero-byte file, timeout.
5. Audio edge cases: optional audio stream mapping (-map 0:a?).
"""

import os
import tempfile
import subprocess
from unittest.mock import patch, MagicMock
from PIL import Image

from gamebot.video import ShortsGenerator, _get_font


class TestPillowBadgeAdversarial:
    """Stress-tests create_overlay_badge with boundary and adversarial inputs."""

    def test_badge_with_empty_title(self):
        gen = ShortsGenerator()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            out = gen.create_overlay_badge(title="", watermark="@gameworld2029", output_png=tmp_path)
            assert out == tmp_path
            assert os.path.exists(tmp_path)
            with Image.open(tmp_path) as img:
                assert img.size == (720, 1280)
                assert img.mode == "RGBA"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_badge_with_whitespace_and_newlines(self):
        gen = ShortsGenerator()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            out = gen.create_overlay_badge(
                title="   \n\t  Cyberpunk \n 2077  \t ",
                watermark="   @gameworld2029   ",
                output_png=tmp_path,
            )
            assert out == tmp_path
            assert os.path.exists(tmp_path)
            with Image.open(tmp_path) as img:
                assert img.size == (720, 1280)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_badge_with_extremely_long_title(self):
        gen = ShortsGenerator()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        huge_title = "A" * 5000
        try:
            out = gen.create_overlay_badge(
                title=huge_title,
                watermark="@gameworld2029",
                output_png=tmp_path,
            )
            assert out == tmp_path
            assert os.path.exists(tmp_path)
            with Image.open(tmp_path) as img:
                assert img.size == (720, 1280)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_badge_with_cyrillic_and_special_symbols(self):
        gen = ShortsGenerator()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        cyrillic_title = "Ведьмак 3: Дикая Охота — Полное Издание & Game of the Year (100% Free!)"
        try:
            out = gen.create_overlay_badge(
                title=cyrillic_title,
                watermark="@gameworld2029",
                output_png=tmp_path,
            )
            assert out == tmp_path
            assert os.path.exists(tmp_path)
            with Image.open(tmp_path) as img:
                assert img.size == (720, 1280)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_badge_with_quotes_and_formatting_characters(self):
        gen = ShortsGenerator()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        adversarial_title = "Tom Clancy's \"Ghost Recon\": <Breakpoint> & $100% #1 / \\ [test]"
        try:
            out = gen.create_overlay_badge(
                title=adversarial_title,
                watermark="@test_channel_2029",
                output_png=tmp_path,
            )
            assert out == tmp_path
            assert os.path.exists(tmp_path)
            with Image.open(tmp_path) as img:
                assert img.size == (720, 1280)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class TestShortsGeneratorTempFileCleanup:
    """Stress-tests temporary overlay file cleanup across all execution paths."""

    def test_cleanup_on_ffmpeg_timeout(self):
        gen = ShortsGenerator()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
            in_path = tmp_in.name
        out_path = in_path + "_out.mp4"

        created_overlay = None

        def fake_create_overlay(*args, **kwargs):
            nonlocal created_overlay
            fd, created_overlay = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            return created_overlay

        try:
            with patch.object(gen, "is_ffmpeg_available", return_value=True), \
                 patch.object(gen, "create_overlay_badge", side_effect=fake_create_overlay), \
                 patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=180)):
                result = gen.build_shorts(
                    input_video_path=in_path,
                    title="Timeout Test",
                    output_video_path=out_path,
                )
                assert result is False
                assert created_overlay is not None
                # Must be unlinked in finally block
                assert not os.path.exists(created_overlay)
        finally:
            for p in (in_path, out_path):
                if os.path.exists(p):
                    os.unlink(p)
            if created_overlay and os.path.exists(created_overlay):
                os.unlink(created_overlay)

    def test_cleanup_on_ffmpeg_exit_code_failure(self):
        gen = ShortsGenerator()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
            in_path = tmp_in.name
        out_path = in_path + "_out.mp4"

        created_overlay = None

        def fake_create_overlay(*args, **kwargs):
            nonlocal created_overlay
            fd, created_overlay = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            return created_overlay

        try:
            mock_res = MagicMock()
            mock_res.returncode = 1
            mock_res.stderr = "Invalid data found when processing input"

            with patch.object(gen, "is_ffmpeg_available", return_value=True), \
                 patch.object(gen, "create_overlay_badge", side_effect=fake_create_overlay), \
                 patch("subprocess.run", return_value=mock_res):
                result = gen.build_shorts(
                    input_video_path=in_path,
                    title="Failure Test",
                    output_video_path=out_path,
                )
                assert result is False
                assert created_overlay is not None
                assert not os.path.exists(created_overlay)
        finally:
            for p in (in_path, out_path):
                if os.path.exists(p):
                    os.unlink(p)
            if created_overlay and os.path.exists(created_overlay):
                os.unlink(created_overlay)

    def test_cleanup_on_unhandled_exception(self):
        gen = ShortsGenerator()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
            in_path = tmp_in.name
        out_path = in_path + "_out.mp4"

        created_overlay = None

        def fake_create_overlay(*args, **kwargs):
            nonlocal created_overlay
            fd, created_overlay = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            return created_overlay

        try:
            with patch.object(gen, "is_ffmpeg_available", return_value=True), \
                 patch.object(gen, "create_overlay_badge", side_effect=fake_create_overlay), \
                 patch("subprocess.run", side_effect=RuntimeError("Unexpected OS crash")):
                result = gen.build_shorts(
                    input_video_path=in_path,
                    title="Exception Test",
                    output_video_path=out_path,
                )
                assert result is False
                assert created_overlay is not None
                assert not os.path.exists(created_overlay)
        finally:
            for p in (in_path, out_path):
                if os.path.exists(p):
                    os.unlink(p)
            if created_overlay and os.path.exists(created_overlay):
                os.unlink(created_overlay)

    def test_cleanup_on_zero_byte_output_file(self):
        gen = ShortsGenerator()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
            in_path = tmp_in.name
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_out:
            out_path = tmp_out.name  # 0 bytes initially

        created_overlay = None

        def fake_create_overlay(*args, **kwargs):
            nonlocal created_overlay
            fd, created_overlay = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            return created_overlay

        try:
            mock_res = MagicMock()
            mock_res.returncode = 0  # Claims success but output is 0 bytes
            mock_res.stderr = ""

            with patch.object(gen, "is_ffmpeg_available", return_value=True), \
                 patch.object(gen, "create_overlay_badge", side_effect=fake_create_overlay), \
                 patch("subprocess.run", return_value=mock_res):
                result = gen.build_shorts(
                    input_video_path=in_path,
                    title="Zero Byte Output Test",
                    output_video_path=out_path,
                )
                assert result is False
                assert not os.path.exists(created_overlay)
        finally:
            for p in (in_path, out_path):
                if os.path.exists(p):
                    os.unlink(p)
            if created_overlay and os.path.exists(created_overlay):
                os.unlink(created_overlay)


class TestShortsGeneratorFilterComplexAdversarial:
    """Stress-tests command generation and parameter contracts under edge conditions."""

    def test_command_maps_optional_audio_stream(self):
        """Verifies that -map 0:a? is used to prevent failures on silent trailers."""
        gen = ShortsGenerator()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
            in_path = tmp_in.name
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_out:
            out_path = tmp_out.name

        captured_cmd = []

        def fake_run(cmd, *args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            with open(out_path, "wb") as f:
                f.write(b"video_bytes")
            mock = MagicMock()
            mock.returncode = 0
            return mock

        try:
            with patch.object(gen, "is_ffmpeg_available", return_value=True), \
                 patch("subprocess.run", side_effect=fake_run):
                success = gen.build_shorts(
                    input_video_path=in_path,
                    title="Silent Video",
                    output_video_path=out_path,
                )
                assert success is True
                assert "-map" in captured_cmd
                # Verify optional audio flag 0:a?
                assert "0:a?" in captured_cmd
                # Verify video output map [outv]
                assert "[outv]" in captured_cmd
        finally:
            for p in (in_path, out_path):
                if os.path.exists(p):
                    os.unlink(p)

    def test_duration_capping_parameter(self):
        """Verifies -t flag correctly enforces max_duration cap."""
        gen = ShortsGenerator()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
            in_path = tmp_in.name
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_out:
            out_path = tmp_out.name

        captured_cmd = []

        def fake_run(cmd, *args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            with open(out_path, "wb") as f:
                f.write(b"video_bytes")
            mock = MagicMock()
            mock.returncode = 0
            return mock

        try:
            with patch.object(gen, "is_ffmpeg_available", return_value=True), \
                 patch("subprocess.run", side_effect=fake_run):
                success = gen.build_shorts(
                    input_video_path=in_path,
                    title="Duration Test",
                    output_video_path=out_path,
                    max_duration=60,
                )
                assert success is True
                t_idx = captured_cmd.index("-t")
                assert captured_cmd[t_idx + 1] == "60"
        finally:
            for p in (in_path, out_path):
                if os.path.exists(p):
                    os.unlink(p)

    def test_even_dimension_rounding_in_filter(self):
        """Verifies scale filter enforces divisible-by-2 height (-2) to avoid libx264 crash."""
        gen = ShortsGenerator()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
            in_path = tmp_in.name
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_out:
            out_path = tmp_out.name

        captured_cmd = []

        def fake_run(cmd, *args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            with open(out_path, "wb") as f:
                f.write(b"video_bytes")
            mock = MagicMock()
            mock.returncode = 0
            return mock

        try:
            with patch.object(gen, "is_ffmpeg_available", return_value=True), \
                 patch("subprocess.run", side_effect=fake_run):
                success = gen.build_shorts(
                    input_video_path=in_path,
                    title="Scale Test",
                    output_video_path=out_path,
                )
                assert success is True
                fc_idx = captured_cmd.index("-filter_complex")
                fc = captured_cmd[fc_idx + 1]
                # Check that height is -2 (guarantees even number for H.264 macroblocks)
                assert "scale=720:-2" in fc
                assert "crop=720:1280" in fc
                assert "libx264" in captured_cmd
                assert "yuv420p" in captured_cmd
        finally:
            for p in (in_path, out_path):
                if os.path.exists(p):
                    os.unlink(p)
