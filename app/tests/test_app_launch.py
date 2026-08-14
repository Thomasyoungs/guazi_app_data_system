import unittest

from guazi_app_data_system.app_startup import find_text_bounds


class AppLaunchTest(unittest.TestCase):
    def test_find_text_bounds_from_uiautomator_xml(self):
        xml = """
        <hierarchy>
          <node text="价格从低到高" bounds="[10,20][110,60]" />
        </hierarchy>
        """

        self.assertEqual(find_text_bounds(xml, "价格从低到高"), (10, 20, 110, 60))


if __name__ == "__main__":
    unittest.main()
