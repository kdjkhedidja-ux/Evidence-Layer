"""Read-only connector adapters.

This package is a connector SDK slice: OAuth acquisition, encrypted token
storage, sync workers, and database ingestion remain separate product work.
"""

from .base import (
    ConnectorDocument,
    ConnectorError,
    ConnectorItem,
    ConnectorPage,
    EvidenceConnector,
)
from .confluence import ConfluenceConfig, ConfluenceConnector
from .dropbox import DropboxConfig, DropboxConnector
from .github import GitHubConfig, GitHubConnector
from .google_drive import GoogleDriveConfig, GoogleDriveConnector
from .notion import NotionConfig, NotionConnector

CONNECTOR_TYPES = {
    "google_drive": GoogleDriveConnector,
    "github": GitHubConnector,
    "dropbox": DropboxConnector,
    "confluence": ConfluenceConnector,
    "notion": NotionConnector,
}

__all__ = [
    "CONNECTOR_TYPES",
    "ConfluenceConfig",
    "ConfluenceConnector",
    "ConnectorDocument",
    "ConnectorError",
    "ConnectorItem",
    "ConnectorPage",
    "DropboxConfig",
    "DropboxConnector",
    "EvidenceConnector",
    "GitHubConfig",
    "GitHubConnector",
    "GoogleDriveConfig",
    "GoogleDriveConnector",
    "NotionConfig",
    "NotionConnector",
]
