"""Placeholder for a real partner bank/MFI or wallet connector.

Deliberately unimplemented: there is no approved partner API specification,
agreement or credential yet. Implement SourceConnector here once those exist,
keeping the same interface so the merchant application doesn't change."""

from .base import ConnectorNotConfigured, SourceConnector


class PartnerBankConnector(SourceConnector):
    key = "partner_bank"
    name = "Partner bank (not configured)"
    is_demo = False

    def _unavailable(self, *args, **kwargs):
        raise ConnectorNotConfigured(
            "No live partner connector is configured. It requires an approved data-sharing agreement, "
            "the partner's API specification and institution-issued credentials."
        )

    list_accounts = fetch_events = fetch_balances = _unavailable
