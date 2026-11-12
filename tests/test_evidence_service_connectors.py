from evidence_service import app as app_module


def test_connector_runtime_symbols_are_imported():
    for name in (
        "TokenVault",
        "TokenVaultUnavailable",
        "ConnectorRepository",
        "ConnectorDenied",
        "ConnectorNotFound",
        "ConnectorUnavailable",
        "GoogleDriveSyncService",
        "NotionSyncService",
        "GitHubAppSyncService",
        "ConnectorAuthUnavailable",
        "ConnectorSyncError",
        "NotionSyncError",
    ):
        assert hasattr(app_module, name), name
