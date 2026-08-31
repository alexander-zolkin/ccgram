"""Tests for file_handler helper functions."""

import re
import unicodedata
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram.error import TimedOut

from ccgram.handlers.file_handler import (
    _fetch_to_dest,
    _generate_photo_filename,
    _sanitize_caption,
    _sanitize_filename,
    _unique_dest,
    _upload_and_notify,
    _validate_dest_path,
)

_FH = "ccgram.handlers.file_handler"


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("input_name", "expected"),
        [
            # ASCII names pass through untouched
            ("document.pdf", "document.pdf"),
            ("file-name_123.txt", "file-name_123.txt"),
            # path traversal is stripped to the basename
            ("/etc/passwd", "passwd"),
            ("../../../etc/passwd", "passwd"),
            ("../../etc/passwd", "passwd"),
            # punctuation and whitespace still collapse to underscores
            ("hello world!.txt", "hello_world_.txt"),
            ("file@#$.txt", "file___.txt"),
            ("quote'and\"double.txt", "quote_and_double.txt"),
            ("semi;colon&amp.sh", "semi_colon_amp.sh"),
            ("tab\tand\nnewline.txt", "tab_and_newline.txt"),
            # names that sanitize down to nothing usable
            ("..", "unnamed"),
            (".", "unnamed"),
            ("...", "unnamed"),
            ("", "unnamed"),
            # non-Latin scripts survive, which is the point of this test
            ("Отчёт за 2025.pdf", "Отчёт_за_2025.pdf"),
            ("договор.docx", "договор.docx"),
            ("Λογαριασμός.pdf", "Λογαριασμός.pdf"),
            ("请求书.xlsx", "请求书.xlsx"),
            ("領収書.pdf", "領収書.pdf"),
            ("فاتورة.pdf", "فاتورة.pdf"),
            ("חשבונית.pdf", "חשבונית.pdf"),
            # Thai writes vowels as combining marks, which are not word
            # characters, so a \\w-only rule would punch holes in the word
            ("ใบเสร็จ.pdf", "ใบเสร็จ.pdf"),
            ("नमस्ते.pdf", "नमस्ते.pdf"),
            ("송장.pdf", "송장.pdf"),
            # mixed scripts and digits keep both halves
            ("invoice-Счёт-2025.pdf", "invoice-Счёт-2025.pdf"),
            ("café.txt", "café.txt"),
            # non-word characters go regardless of script
            ("Счёт №5-2025.pdf", "Счёт__5-2025.pdf"),
            ("файл (копия).txt", "файл__копия_.txt"),
            # emoji and other symbols are not word characters
            ("report📊.pdf", "report_.pdf"),
            # bidi override cannot be smuggled into the saved name
            ("safe‮gnp.exe", "safe_gnp.exe"),
            # a traversal attempt written in Cyrillic is still a basename
            ("../отчёты/итог.pdf", "итог.pdf"),
        ],
    )
    def test_sanitize(self, input_name: str, expected: str) -> None:
        assert _sanitize_filename(input_name) == expected

    def test_composes_before_substituting(self) -> None:
        """A decomposed name keeps its accents.

        macOS reports names in NFD, where the combining mark is a separate
        character and not a word character, so without composing first it
        would be replaced on its own and leave a bare letter behind.
        """
        decomposed = unicodedata.normalize("NFD", "Отчёт.pdf")
        assert decomposed != "Отчёт.pdf"
        assert _sanitize_filename(decomposed) == "Отчёт.pdf"

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("Отчёт.pdf", "Договор.pdf"),
            ("请求书.xlsx", "领收书.xlsx"),
            ("فاتورة.pdf", "ايصال.pdf"),
        ],
    )
    def test_distinct_non_latin_names_stay_distinct(
        self, first: str, second: str
    ) -> None:
        """Two names of equal length used to sanitize to the same underscores.

        The saved file then shadowed the earlier one, and the message quoting
        the path said nothing about which document it was.
        """
        assert _sanitize_filename(first) != _sanitize_filename(second)
        assert "_" not in _sanitize_filename(first)

    def test_truncates_long_names_preserving_extension(self) -> None:
        long = "a" * 250 + ".pdf"
        result = _sanitize_filename(long)
        assert len(result) <= 200
        assert result.endswith(".pdf")

    def test_truncates_multibyte_name_by_encoded_size(self) -> None:
        result = _sanitize_filename("界" * 200 + ".pdf")
        assert len(result.encode()) <= 200
        assert result.endswith(".pdf")


class TestUniqueDest:
    def test_returns_original_if_not_exists(self, tmp_path: Path) -> None:
        assert _unique_dest(tmp_path / "file.txt") == tmp_path / "file.txt"

    @pytest.mark.parametrize(
        ("existing_files", "expected_name"),
        [
            (["file.txt"], "file_1.txt"),
            (["file.txt", "file_1.txt", "file_2.txt"], "file_3.txt"),
            (["file"], "file_1"),
        ],
    )
    def test_increments_suffix(
        self, tmp_path: Path, existing_files: list[str], expected_name: str
    ) -> None:
        for name in existing_files:
            (tmp_path / name).write_text("x")
        assert _unique_dest(tmp_path / existing_files[0]) == tmp_path / expected_name

    def test_fallback_to_timestamp_after_100(self, tmp_path: Path) -> None:
        dest = tmp_path / "file.txt"
        for i in range(100):
            name = "file.txt" if i == 0 else f"file_{i}.txt"
            (tmp_path / name).write_text(str(i))
        result = _unique_dest(dest)
        assert result.name.startswith("file_") and result.name.endswith(".txt")
        assert result != dest

    def test_broken_symlink_treated_as_existing(self, tmp_path: Path) -> None:
        dest = tmp_path / "file.txt"
        dest.symlink_to(tmp_path / "nonexistent_target")
        assert _unique_dest(dest) == tmp_path / "file_1.txt"


class TestValidateDestPath:
    @pytest.mark.parametrize(
        ("rel_dest", "expected"),
        [
            ("file.txt", True),
            ("subdir/file.txt", True),
            ("../outside.txt", False),
        ],
    )
    def test_path_validation(
        self, tmp_path: Path, rel_dest: str, expected: bool
    ) -> None:
        upload = tmp_path / "upload"
        upload.mkdir()
        if "/" in rel_dest and not rel_dest.startswith(".."):
            (upload / Path(rel_dest).parent).mkdir(parents=True, exist_ok=True)
        assert _validate_dest_path(upload / rel_dest, upload) is expected

    def test_rejects_absolute_path_outside(self, tmp_path: Path) -> None:
        upload = tmp_path / "upload"
        upload.mkdir()
        assert _validate_dest_path(tmp_path / "outside.txt", upload) is False


class TestSanitizeCaption:
    @pytest.mark.parametrize(
        ("input_text", "expected"),
        [
            ("", ""),
            ("hello\x00\x01\x02world", "helloworld"),
            ("hello\x07\x1bworld", "helloworld"),
            ("line1\nline2\r\nline3\ttab", "line1 line2  line3\ttab"),
        ],
    )
    def test_sanitize(self, input_text: str, expected: str) -> None:
        assert _sanitize_caption(input_text) == expected

    def test_limits_to_500_chars(self) -> None:
        assert len(_sanitize_caption("a" * 600)) == 500


class TestGeneratePhotoFilename:
    def test_format(self) -> None:
        result = _generate_photo_filename("ABCDEFGHIJKLMNOP")
        assert re.match(r"^photo_\d{8}_\d{6}_ABCDEFGH\.jpg$", result)


class TestUnboundFileFirst:
    """CCGRAM-HOTFIX:file-first-unbound — a file dropped into a fresh topic must
    open the topic via the same wizard as text, not dead-end with an error."""

    @patch(f"{_FH}.send_telegram_to_window", new_callable=AsyncMock)
    @patch(f"{_FH}.thread_router")
    @patch("ccgram.handlers.text.text_handler._handle_unbound_topic")
    @patch(f"{_FH}._download_and_save", new_callable=AsyncMock)
    @patch(f"{_FH}._resolve_upload_dir")
    @patch(f"{_FH}.safe_reply", new_callable=AsyncMock)
    async def test_unbound_routes_into_wizard(
        self,
        mock_reply: AsyncMock,
        mock_resolve: MagicMock,
        mock_download: AsyncMock,
        mock_wizard: AsyncMock,
        _mock_tr: MagicMock,
        _mock_send: AsyncMock,
    ) -> None:
        # Unbound topic: window_id is None.
        mock_resolve.return_value = (None, None, "No session bound to this topic.")
        mock_download.return_value = "photo_x.jpg"
        mock_wizard.return_value = True  # wizard handled it

        message = MagicMock()
        message.caption = None
        context = MagicMock()
        context.user_data = {}

        await _upload_and_notify(
            message,
            context,
            user_id=100,
            thread_id=42,
            filename="photo_x.jpg",
            file_id="fid",
            file_size=123,
            size_label="Photo",
            claude_msg_tpl="I've uploaded an image to {path} — please take a look.",
            success_emoji="📷",
        )

        # No "No session bound" error reply.
        mock_reply.assert_not_called()
        # Wizard invoked with the notify text carrying the ABSOLUTE staged path.
        mock_wizard.assert_awaited_once()
        args = mock_wizard.await_args.args
        assert args[0] == 100  # user_id
        assert args[1] == 42  # thread_id
        notify_text = args[2]
        assert "please take a look" in notify_text
        assert "pending-uploads/42/photo_x.jpg" in notify_text
        assert Path(notify_text.split(" to ")[1].split(" —")[0]).is_absolute()

    @patch(f"{_FH}._download_and_save", new_callable=AsyncMock)
    @patch(f"{_FH}._resolve_upload_dir")
    @patch(f"{_FH}.safe_reply", new_callable=AsyncMock)
    async def test_general_topic_none_thread_still_errors(
        self,
        mock_reply: AsyncMock,
        mock_resolve: MagicMock,
        mock_download: AsyncMock,
    ) -> None:
        # No thread (General topic) — keep the old explicit error, no staging.
        mock_resolve.return_value = (None, None, "No session bound to this topic.")
        message = MagicMock()
        context = MagicMock()

        await _upload_and_notify(
            message,
            context,
            user_id=100,
            thread_id=None,
            filename="photo_x.jpg",
            file_id="fid",
            file_size=123,
            size_label="Photo",
            claude_msg_tpl="I've uploaded an image to {path}.",
            success_emoji="📷",
        )

        mock_download.assert_not_called()
        mock_reply.assert_awaited_once()
        assert "No session bound" in mock_reply.await_args.args[1]


class TestFetchToDest:
    """CCGRAM-HOTFIX:file-dl-timeouts — a stalled connection through the VPN
    proxy used to fail the upload outright; the transport half now retries."""

    @staticmethod
    def _bot(get_file: AsyncMock) -> MagicMock:
        message = MagicMock()
        message.get_bot.return_value.get_file = get_file
        return message

    @patch(f"{_FH}.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_then_succeeds(
        self, mock_sleep: AsyncMock, tmp_path: Path
    ) -> None:
        download = AsyncMock(side_effect=[TimedOut(), None])
        get_file = AsyncMock(return_value=MagicMock(download_to_drive=download))

        await _fetch_to_dest(self._bot(get_file), "fid", tmp_path / "f.jpg")

        assert get_file.await_count == 2
        assert download.await_count == 2
        mock_sleep.assert_awaited_once_with(1.0)

    @patch(f"{_FH}.asyncio.sleep", new_callable=AsyncMock)
    async def test_reraises_after_last_attempt(
        self, mock_sleep: AsyncMock, tmp_path: Path
    ) -> None:
        download = AsyncMock(side_effect=TimedOut())
        get_file = AsyncMock(return_value=MagicMock(download_to_drive=download))

        with pytest.raises(TimedOut):
            await _fetch_to_dest(self._bot(get_file), "fid", tmp_path / "f.jpg")

        assert download.await_count == 3
        assert [c.args[0] for c in mock_sleep.await_args_list] == [1.0, 3.0]

    @patch(f"{_FH}.asyncio.sleep", new_callable=AsyncMock)
    async def test_discards_partial_file_between_attempts(
        self, _mock_sleep: AsyncMock, tmp_path: Path
    ) -> None:
        dest = tmp_path / "f.jpg"
        attempts = 0

        async def download_to_drive(path: str) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                # First attempt writes a partial body, then the connection stalls.
                Path(path).write_bytes(b"partial")
                raise TimedOut()

        get_file = AsyncMock(
            return_value=MagicMock(download_to_drive=AsyncMock(wraps=download_to_drive))
        )

        await _fetch_to_dest(self._bot(get_file), "fid", dest)

        assert attempts == 2
        # The partial body from the failed attempt must not survive.
        assert not dest.exists()

    async def test_no_retry_on_success(self, tmp_path: Path) -> None:
        download = AsyncMock(return_value=None)
        get_file = AsyncMock(return_value=MagicMock(download_to_drive=download))

        await _fetch_to_dest(self._bot(get_file), "fid", tmp_path / "f.jpg")

        assert get_file.await_count == 1
