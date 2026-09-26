"""High-fidelity office document previews for the local workbench."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from .errors import DomainError
from .config import settings


_lock_guard = threading.Lock()
_preview_locks: dict[str, threading.Lock] = {}


_WORD_EXPORT_SCRIPT = r"""
param([string]$InputPath, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
$word = $null
$documents = $null
$document = $null
$options = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $options = $word.Options
    $options.UpdateLinksAtOpen = $false
    $documents = $word.Documents
    $document = $documents.Open($InputPath, $false, $true)
    $document.ExportAsFixedFormat($OutputPath, 17)
}
finally {
    if ($null -ne $document) {
        try { $document.Close($false) } catch { }
        try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) } catch { }
    }
    if ($null -ne $documents) {
        try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($documents) } catch { }
    }
    if ($null -ne $options) {
        try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($options) } catch { }
    }
    if ($null -ne $word) {
        try { $word.Quit() } catch { }
        try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) } catch { }
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"""


def _run(command: list[str], timeout: int = 60) -> None:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                            check=False, creationflags=flags)
    if result.returncode:
        detail = (result.stderr or result.stdout or "document converter failed").strip()
        raise RuntimeError(detail[-1000:])


def _word_to_pdf(source: Path, target: Path, work: Path) -> None:
    script = work / "export-word-preview.ps1"
    script.write_text(_WORD_EXPORT_SCRIPT, encoding="utf-8")
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise RuntimeError("Microsoft Word preview converter is unavailable")
    _run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
          "-File", str(script), str(source), str(target)])


def _libreoffice_to_pdf(source: Path, target: Path, work: Path) -> None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice preview converter is unavailable")
    _run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(work), str(source)])
    generated = work / (source.stem + ".pdf")
    if generated != target and generated.exists():
        generated.replace(target)


def _lock_for(digest: str) -> threading.Lock:
    with _lock_guard:
        return _preview_locks.setdefault(digest, threading.Lock())


def _cached_pdf(digest: str) -> Path:
    root = Path(settings().file_local_root).resolve().parent / "preview-cache"
    return root / f"{digest}.pdf"


def docx_to_pdf(data: bytes, digest: str | None = None) -> bytes:
    """Render a DOCX through the installed office engine, preserving Word layout."""
    digest = digest or sha256(data).hexdigest()
    cache = _cached_pdf(digest)
    try:
        with _lock_for(digest):
            if cache.exists():
                rendered = cache.read_bytes()
                if rendered.startswith(b"%PDF-"):
                    return rendered
            with TemporaryDirectory(prefix="moldpilot-docx-preview-") as directory:
                work = Path(directory)
                source, target = work / "source.docx", work / "preview.pdf"
                source.write_bytes(data)
                if os.name == "nt":
                    _word_to_pdf(source, target, work)
                else:
                    _libreoffice_to_pdf(source, target, work)
                rendered = target.read_bytes()
                if not rendered.startswith(b"%PDF-"):
                    raise RuntimeError("converter did not produce a valid PDF")
                cache.parent.mkdir(parents=True, exist_ok=True)
                temporary = cache.with_suffix(f".{os.getpid()}.tmp")
                temporary.write_bytes(rendered);temporary.replace(cache)
                return rendered
    except (OSError, subprocess.SubprocessError, RuntimeError):
        raise DomainError("PREVIEW_RENDER_FAILED", "Word 文档暂时无法高保真预览，请下载原件查看", 503)
