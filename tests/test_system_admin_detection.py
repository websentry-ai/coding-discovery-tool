"""Root detection must recognize NT AUTHORITY\\SYSTEM, not only Administrators-group
membership — otherwise an MDM/all-users scan run as SYSTEM collapses to the empty
systemprofile and returns 0 inventory."""
import unittest
from unittest.mock import patch

from scripts.coding_discovery_tools import windows_extraction_helpers as weh


class SystemAdminDetectionTest(unittest.TestCase):
    def test_system_counts_as_admin_when_isuseranadmin_undetermined(self):
        # IsUserAnAdmin() returns None/False for SYSTEM (not in the Administrators
        # group), but the SID check identifies SYSTEM as root.
        with patch.object(weh, "windows_admin_state", return_value=None), \
             patch.object(weh, "_running_as_local_system", return_value=True):
            self.assertTrue(weh.is_running_as_admin())

    def test_administrators_group_membership_still_admin(self):
        with patch.object(weh, "windows_admin_state", return_value=True):
            self.assertTrue(weh.is_running_as_admin())

    def test_plain_user_is_not_admin(self):
        with patch.object(weh, "windows_admin_state", return_value=False), \
             patch.object(weh, "_running_as_local_system", return_value=False):
            self.assertFalse(weh.is_running_as_admin())


if __name__ == "__main__":
    unittest.main()
