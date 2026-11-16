"""The files a student attached: turned into text once, and added to the task the roles read.

Plain text and Word files are read here. Photos and PDFs go to the reader role. Any other type, or
a file that cannot be read, becomes a line saying so: a failed read never fails the round.
"""

import asyncio
import io
import logging
import zipfile
from collections.abc import Callable
from typing import Literal
from xml.etree import ElementTree

from pydantic_ai.models import Model

from agent.context import ContextClient
from agent.roles.reader import transcribe
from shared.context import Task, TaskFile

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 100_000  # what is kept of a file, and the most the API saves
MAX_PARALLEL = 3  # files read at once
FILE_TIMEOUT_SECONDS = 90  # fetching and reading one file
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024  # of word/document.xml once unpacked: a zip bomb guard

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
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
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

Kind = Literal["plain", "docx", "model"]


class UnreadableError(Exception):
    """A file that has no text this agent can read."""


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


def _blocks(parent: ElementTree.Element, lines: list[str]) -> None:
    for child in parent:
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
        elif child.tag in (f"{W}sdt", f"{W}sdtContent"):  # content controls wrap other blocks
            _blocks(child, lines)


def docx_text(data: bytes) -> str:
    """The paragraphs of a Word file in order, and each table row on one line. Raises
    UnreadableError for a file that is not Word, or whose text unpacks past the limit."""
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
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:  # a Word file has neither
        raise UnreadableError("document.xml has a DTD")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as error:
        raise UnreadableError("document.xml is not valid XML") from error
    body = root.find(f"{W}body")
    lines: list[str] = []
    if body is not None:
        _blocks(body, lines)
    return "\n".join(lines)


async def _read(
    file: TaskFile, media_type: str, kind: Kind, context: ContextClient, reader: Model
) -> str:
    data, _ = await context.get_file(file.id)
    if kind == "plain":
        return data.decode("utf-8", errors="replace")
    if kind == "docx":
        return await asyncio.to_thread(docx_text, data)
    return await transcribe(reader, data, media_type)


def unreadable_line(file: TaskFile) -> str:
    name = " ".join(file.name.split())[:200]
    return f'[The student attached "{name}" ({file.mime_type}), which could not be read.]'


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


async def task_text_with_files(
    task: Task, context: ContextClient, reader: Model, may_save: Callable[[], bool]
) -> str:
    """The task as the roles read it: the student's text, then each file as a block with its text,
    or with a line saying it could not be read. A task without files is its text."""
    if not task.files:
        return task.text
    gate = asyncio.Semaphore(MAX_PARALLEL)
    texts = await asyncio.gather(
        *(_text_of(file, context, reader, gate, may_save) for file in task.files)
    )
    parts = [task.text] if task.text.strip() else []
    for number, (file, text) in enumerate(zip(task.files, texts, strict=True), start=1):
        name = " ".join(file.name.split())[:200]
        body = unreadable_line(file) if text is None else text
        parts.append(f"--- Attached file {number}: {name} ---\n{body}")
    return "\n\n".join(parts)
