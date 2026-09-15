from __future__ import annotations

import unittest

from integration.onboarding.salesforce_detail_provider import (DisabledSalesforceDetailProvider,
                                                                SalesforceDetailProviderBlockedError,
                                                                SalesforceDetailProviderStatus)


class SalesforceDetailProviderTests(unittest.TestCase):
    def test_default_provider_has_no_source_access_or_generic_query(self):
        provider = DisabledSalesforceDetailProvider()
        self.assertEqual(provider.health(), SalesforceDetailProviderStatus.NOT_CONFIGURED)
        self.assertFalse(hasattr(provider, "query"))
        self.assertFalse(hasattr(provider, "write"))
        with self.assertRaisesRegex(SalesforceDetailProviderBlockedError, "not_configured"):
            provider.read_co_0717()
