"""Make the paper's released code citable: CITATION.cff, .zenodo.json and codemeta.json,
and a check that the code is archived with a DOI the paper cites (software_citation)."""

from .workflow import software_citation

__all__ = ["software_citation"]
