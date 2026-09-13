import re
from importlib.metadata import version
from typing import Protocol

import trafilatura


class Extractor(Protocol):
    name: str
    version: str

    def extract(self, html: bytes) -> str: ...


class EmptyExtraction(ValueError):
    pass


def normalize_text(text: str) -> str:
    lines = [
        re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\r\n", "\n").split("\n")
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class TrafilaturaExtractor:
    name = "trafilatura"
    version = version("trafilatura")

    def extract(self, html: bytes) -> str:
        result = trafilatura.extract(
            html, include_comments=False, include_images=False, output_format="txt"
        )
        text = normalize_text(result or "")
        if not text:
            raise EmptyExtraction("No readable article text was found")
        return text
