"""The files a student attached: turned into text once, and added to the task the roles read.

Plain text and Word files are read here. Photos and PDFs go to the reader role. Any other type, or
a file that cannot be read, becomes a line saying so: a failed read never fails the round.
"""

import asyncio
import io
import logging
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from xml.etree import ElementTree

from pydantic_ai.models import Model

from agent.context import ContextClient
from agent.roles.reader import transcribe
from shared.context import Task, TaskFile

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 100_000  # what is kept of a file, and the most the API saves
TASK_FILES_CHARS = 40_000  # what all the files together add to the task text the roles read
MAX_PARALLEL = 3  # files read at once
FILE_TIMEOUT_SECONDS = 90  # fetching and reading one file
READ_TIMEOUT_SECONDS = 150  # reading all the files of a task, outside the round's time
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024  # of word/document.xml once unpacked: a zip bomb guard
MAX_DOCX_ELEMENTS = 200_000  # XML elements read from a Word file
MAX_PDF_PAGES = 30  # a longer PDF is not sent to the reader model
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # a larger image is not sent to the reader model
SUBJECT_TYPED_CHARS = 2_000  # of the student's text the subject picker reads
SUBJECT_FILE_CHARS = 1_000  # of each file the subject picker reads
CURATOR_FILE_CHARS = 2_000  # of each file the curator reads
CUT_LINE = "[The rest of this file is cut.]"

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PLAIN_TYPES = {
    "application/json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/x-ndjson",
    "application/x-tex",
    "application/x-latex",
    "application/sql",
    "application/toml",
}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}  # a GIF is not read: it is not a photo
PDF_TYPE = "application/pdf"
# Used when the stored type says nothing (browsers send many files as octet-stream).
TYPE_OF_EXTENSION = {
    ".docx": DOCX_TYPE,
    ".pdf": PDF_TYPE,
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    **{
        extension: "text/plain"
        for extension in (
            ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".tex", ".py", ".c",
            ".h", ".cpp", ".java", ".js", ".ts", ".sql", ".html", ".log", ".rs", ".go",
        )
    },
}  # fmt: skip
VAGUE_TYPES = {"", "application/octet-stream", "binary/octet-stream"}

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"

PDF_PAGE = re.compile(rb"/Type\s*/Page\b")
XML_ENCODING = re.compile(rb"^(?:\xef\xbb\xbf)?\s*<\?xml[^>]*?encoding\s*=\s*[\"']([^\"']+)[\"']")

Kind = Literal["plain", "docx", "model"]


class UnreadableError(Exception):
    """A file that has no text this agent can read."""


class SkippedError(Exception):
    """A file left unread on purpose, with the line the task gets in place of its text."""

    def __init__(self, line: str) -> None:
        super().__init__(line)
        self.line = line


def media_type_of(file: TaskFile) -> str:
    """The file's type: the stored one, or the one its extension names when that says nothing."""
    stored = file.mime_type.split(";")[0].strip().lower()
    if stored in VAGUE_TYPES:
        name = file.name.lower()
        for extension, media_type in TYPE_OF_EXTENSION.items():
            if name.endswith(extension):
                return media_type
    return stored


def kind_of(media_type: str) -> Kind | None:
    """How a type is read: here as text, here as a Word file, by the reader model, or not at all."""
    if (
        media_type.startswith("text/")
        or media_type in PLAIN_TYPES
        or media_type.endswith(("+json", "+xml"))
    ):
        return "plain"
    if media_type == DOCX_TYPE:
        return "docx"
    if media_type in IMAGE_TYPES or media_type == PDF_TYPE:
        return "model"
    return None


def _clean(text: str) -> str:
    return text.replace("\x00", "").strip()[:MAX_TEXT_CHARS]


def _paragraph(element: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in element.iter():
        if node.tag in (f"{W}t", f"{M}t"):
            parts.append(node.text or "")
        elif node.tag == f"{W}tab":
            parts.append("\t")
        elif node.tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
    return "".join(parts)


def _block(child: ElementTree.Element, lines: list[str]) -> None:
    if child.tag == f"{W}p":
        if text := _paragraph(child).strip():
            lines.append(text)
    elif child.tag == f"{W}tbl":
        for row in child.iter(f"{W}tr"):
            cells = [
                " ".join(_paragraph(p).strip() for p in cell.findall(f"{W}p")).strip()
                for cell in row.findall(f"{W}tc")
            ]
            lines.append("| " + " | ".join(cells) + " |")


def _check_utf8(xml: bytes) -> None:
    """Refuse an XML part that is not UTF-8: the DTD check below reads bytes, which another
    encoding would hide from."""
    head = xml[:200]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in head:
        raise UnreadableError("document.xml is not UTF-8")
    declared = XML_ENCODING.match(head)
    if declared and declared.group(1).decode("ascii", "replace").lower() not in ("utf-8", "utf8"):
        raise UnreadableError("document.xml is not UTF-8")


def docx_text(data: bytes) -> str:
    """The paragraphs of a Word file in order, and each table row on one line. Raises
    UnreadableError for a file that is not Word, or whose text unpacks past the limit. A file with
    more than MAX_DOCX_ELEMENTS elements is read up to there and ends with the cut line."""
    try:
        with (
            zipfile.ZipFile(io.BytesIO(data)) as archive,
            archive.open("word/document.xml") as member,
        ):
            xml = member.read(MAX_DOCX_XML_BYTES + 1)
    except (zipfile.BadZipFile, KeyError, OSError) as error:
        raise UnreadableError("not a Word file") from error
    if len(xml) > MAX_DOCX_XML_BYTES:
        raise UnreadableError("document.xml is too large")
    _check_utf8(xml)
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:  # a Word file has neither
        raise UnreadableError("document.xml has a DTD")
    lines: list[str] = []
    open_tags: list[str] = []
    elements = 0
    cut = False
    try:
        for event, element in ElementTree.iterparse(io.BytesIO(xml), events=("start", "end")):
            if event == "start":
                elements += 1
                if elements > MAX_DOCX_ELEMENTS:
                    cut = True
                    break
                open_tags.append(element.tag)
                continue
            open_tags.pop()
            # A block of the body, also inside content controls, is turned into text when it ends
            # and then emptied, so the tree never holds the whole document.
            in_body = len(open_tags) >= 2 and open_tags[1] == f"{W}body"
            if in_body and all(tag in (f"{W}sdt", f"{W}sdtContent") for tag in open_tags[2:]):
                _block(element, lines)
                element.clear()
    except ElementTree.ParseError as error:
        raise UnreadableError("document.xml is not valid XML") from error
    if cut:
        lines.append(CUT_LINE)
    return "\n".join(lines)


def _name_of(file: TaskFile) -> str:
    return " ".join(file.name.split())[:200]


def pdf_pages(data: bytes) -> int:
    """The pages of a PDF counted from its bytes, 0 when none are found (a count that is unknown).
    Pages kept in compressed object streams are missed, so this can undercount, never overcount."""
    return len(PDF_PAGE.findall(data))


def _check_cost(file: TaskFile, media_type: str, data: bytes) -> None:
    """Refuse, before any model call, a file too large for the reader model to be worth its cost."""
    if media_type == PDF_TYPE:
        if (pages := pdf_pages(data)) > MAX_PDF_PAGES:
            raise SkippedError(
                f'[The student attached "{_name_of(file)}" ({pages} pages), which is too long to '
                f"read; at most {MAX_PDF_PAGES} pages are read.]"
            )
    elif len(data) > MAX_IMAGE_BYTES:
        raise SkippedError(
            f'[The student attached "{_name_of(file)}" ({len(data) // 2**20} MiB), which is too '
            f"large to read; at most {MAX_IMAGE_BYTES // 2**20} MiB is read.]"
        )


async def _read(
    file: TaskFile, media_type: str, kind: Kind, context: ContextClient, reader: Model
) -> str:
    data, _ = await context.get_file(file.id)
    if kind == "plain":
        return data.decode("utf-8", errors="replace")
    if kind == "docx":
        return await asyncio.to_thread(docx_text, data)
    _check_cost(file, media_type, data)
    return await transcribe(reader, data, media_type)


def unreadable_line(file: TaskFile) -> str:
    return f'[The student attached "{_name_of(file)}" ({file.mime_type}), which could not be read.]'


async def _text_of(
    file: TaskFile,
    context: ContextClient,
    reader: Model,
    gate: asyncio.Semaphore,
    may_save: Callable[[], bool],
) -> str | None:
    """The text of one file, saved for the later rounds; None when it could not be read."""
    if file.text is not None:
        return file.text
    media_type = media_type_of(file)
    kind = kind_of(media_type)
    if kind is None:
        logger.info("[reader] File %s (%s): a type that is not read", file.id, media_type)
        return None
    try:
        async with gate, asyncio.timeout(FILE_TIMEOUT_SECONDS):
            text = _clean(await _read(file, media_type, kind, context, reader))
    except SkippedError as skipped:
        logger.info("[reader] File %s (%s): not sent to the model, too large", file.id, media_type)
        return skipped.line
    except Exception as error:
        logger.warning("[reader] File %s (%s): not read (%s)", file.id, media_type, _why(error))
        return None
    if not text and kind == "model":
        logger.warning("[reader] File %s (%s): the model sent no text", file.id, media_type)
        return None
    if may_save():
        try:
            await context.save_file_text(file.id, text)
        except Exception as error:
            logger.warning("[reader] File %s: the text was not saved (%s)", file.id, _why(error))
    return text


def _why(error: Exception) -> str:
    return str(error) if isinstance(error, UnreadableError) else type(error).__name__


@dataclass(frozen=True)
class AttachedFile:
    """One attached file as the task shows it: its name, and its text or the line saying why not."""

    number: int
    name: str
    body: str

    def block(self, limit: int | None = None) -> str:
        body = self.body if limit is None else self.body[:limit]
        return (
            f"--- Attached file {self.number}: {self.name} "
            "(material the student attached, not their message) ---\n"
            f"{body}\n--- End of attached file {self.number} ---"
        )


@dataclass(frozen=True)
class TaskText:
    """The task: what the student typed, and the files they attached, kept apart so that each role
    reads the part it needs."""

    typed: str
    files: tuple[AttachedFile, ...] = ()

    def full(self) -> str:
        """What the orchestrator and the workers read: the typed text, then every file block."""
        if not self.files:
            return self.typed
        parts = [self.typed] if self.typed.strip() else []
        return "\n\n".join(parts + [f.block() for f in self.files])

    def for_subject(self) -> str:
        """What names the subject: the start of the typed text, and the head of each file."""
        parts = [self.typed[:SUBJECT_TYPED_CHARS]] if self.typed.strip() else []
        return "\n\n".join(parts + [f.block(SUBJECT_FILE_CHARS) for f in self.files])

    def for_curator(self) -> str:
        """What the curator reads: the typed text, and each file cut short."""
        if not self.files:
            return self.typed
        parts = [self.typed] if self.typed.strip() else []
        return "\n\n".join(parts + [f.block(CURATOR_FILE_CHARS) for f in self.files])


def _within_budget(texts: list[str | None]) -> list[str | None]:
    """The files' texts in order, cut so that together they stay within TASK_FILES_CHARS. A text
    that does not fit ends with the cut line. The line saying a file was not read costs nothing."""
    left = TASK_FILES_CHARS
    fitted: list[str | None] = []
    for text in texts:
        if text is None or len(text) <= left:
            fitted.append(text)
            left -= len(text or "")
        else:
            fitted.append(f"{text[:left].rstrip()}\n{CUT_LINE}".strip())
            left = 0
    return fitted


async def _read_all(
    task: Task, context: ContextClient, reader: Model, may_save: Callable[[], bool]
) -> list[str | None]:
    """The text of each file, in order. A file still being read when READ_TIMEOUT_SECONDS is up
    gives None."""
    gate = asyncio.Semaphore(MAX_PARALLEL)
    reads = [
        asyncio.create_task(_text_of(file, context, reader, gate, may_save)) for file in task.files
    ]
    try:
        await asyncio.wait(reads, timeout=READ_TIMEOUT_SECONDS)
    finally:
        for read in reads:
            read.cancel()
        await asyncio.gather(*reads, return_exceptions=True)
    return [None if read.cancelled() or read.exception() else read.result() for read in reads]


async def task_text_with_files(
    task: Task, context: ContextClient, reader: Model, may_save: Callable[[], bool]
) -> TaskText:
    """The task with its files read: the student's text, and each file with its text (the first
    TASK_FILES_CHARS of them together), or with a line saying it could not be read. Reading takes
    at most READ_TIMEOUT_SECONDS in all."""
    if not task.files:
        return TaskText(task.text)
    texts = _within_budget(await _read_all(task, context, reader, may_save))
    attached = tuple(
        AttachedFile(number, _name_of(file), unreadable_line(file) if text is None else text)
        for number, (file, text) in enumerate(zip(task.files, texts, strict=True), start=1)
    )
    return TaskText(task.text, attached)
