from evidence_layer.sdk import EvidenceLayerClient, EvidenceApiError


def test_sdk_exposes_the_retrieve_client():
    assert EvidenceLayerClient.__name__ == "EvidenceApiClient"
    assert EvidenceApiError.__name__ == "EvidenceApiError"
