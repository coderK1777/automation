"""Isolated PDF-to-text worker with bounded memory, CPU, pages and output.

Invoked by research.document_text in a separate process. It has no network,
credentials, database or filesystem output responsibilities.
"""
import base64
import io
import json
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    from pypdf import PdfReader
    raw = sys.stdin.buffer.read(650_001)
    if len(raw) > 650_000:
        raise ValueError("PDF input exceeds limit")
    data = base64.b64decode(raw, validate=True)
    if not data.startswith(b"%PDF-"):
        raise ValueError("invalid PDF")
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or len(reader.pages) > 100:
        raise ValueError("encrypted PDF or page limit exceeded")
    pages, count = [], 0
    for index, page in enumerate(reader.pages):
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > 2_000_000:
            raise ValueError("oversized page stream")
        text = page.extract_text() or ""
        count += len(text)
        if count > 250_000:
            raise ValueError("text exceeds limit")
        pages.append(f"[Page {index + 1}]\n{text}")
    if not any(page.split("\n", 1)[-1].strip() for page in pages):
        raise ValueError("image-only document requires OCR")
    print(json.dumps({"text": "\n\n".join(pages), "pages": len(pages)}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Parser diagnostics may contain source text; keep stderr content-free.
        sys.exit(2)
