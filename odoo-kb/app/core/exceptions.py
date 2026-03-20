"""Custom exceptions for the KB service."""


class KBError(Exception):
    """Base exception for KB service."""


class BackendError(KBError):
    """Error communicating with a search backend."""


class IngestionError(KBError):
    """Error during document ingestion."""


class PreprocessingError(KBError):
    """Error during query preprocessing."""


class EmbeddingError(KBError):
    """Error generating embeddings."""


class DocumentNotFoundError(KBError):
    """Requested document does not exist."""
